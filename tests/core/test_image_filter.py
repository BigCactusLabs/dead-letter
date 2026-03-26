from __future__ import annotations

from dead_letter.core.image_filter import filter_images
from dead_letter.core.types import StrippedImageCategory


class TestTrackingPixelDimensionHeuristic:
    """Heuristic 1: width/height of 0 or 1 via HTML attributes or inline CSS."""

    def test_html_attr_width_1_height_1(self) -> None:
        html = '<div><img src="https://t.example.com/pixel.gif" width="1" height="1" /></div>'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" not in result
        assert len(stripped) == 1
        assert stripped[0].category == StrippedImageCategory.TRACKING_PIXEL
        assert stripped[0].reason == "dimension_heuristic"
        assert "t.example.com/pixel.gif" in stripped[0].reference

    def test_html_attr_width_0(self) -> None:
        html = '<img src="https://track.example.com/open" width="0" height="0" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" not in result
        assert len(stripped) == 1

    def test_inline_css_width_1px(self) -> None:
        html = '<img src="https://t.example.com/px" style="width:1px;height:1px" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" not in result
        assert len(stripped) == 1
        assert stripped[0].reason == "dimension_heuristic"

    def test_inline_css_mixed_formats(self) -> None:
        html = '<img src="https://t.example.com/px" style="width: 0px; height: 0" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" not in result

    def test_normal_image_not_stripped(self) -> None:
        html = '<img src="cid:photo1" width="640" height="480" alt="vacation" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert 'src="cid:photo1"' in result
        assert len(stripped) == 0

    def test_small_but_visible_not_stripped(self) -> None:
        """16x16 icons are small but not tracking pixels."""
        html = '<img src="cid:icon" width="16" height="16" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" in result
        assert len(stripped) == 0

    def test_max_width_not_false_positive(self) -> None:
        """Regression: max-width/min-height should not trigger tracking pixel detection."""
        html = '<img src="https://example.com/responsive.png" style="max-width: 0; min-height: 0" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" in result
        assert len(stripped) == 0

    def test_important_css_dimension_still_stripped(self) -> None:
        """Regression: !important should not bypass tracking pixel detection."""
        html = '<img src="https://t.example.com/pixel.gif" style="width: 0 !important; height: 0 !important" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" not in result
        assert len(stripped) == 1
        assert stripped[0].reason == "dimension_heuristic"


class TestTrackingPixelHiddenImages:
    """Heuristic 2: display:none or visibility:hidden."""

    def test_display_none(self) -> None:
        html = '<img src="https://t.example.com/open" style="display:none" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" not in result
        assert stripped[0].reason == "hidden_image"

    def test_visibility_hidden(self) -> None:
        html = '<img src="https://t.example.com/open" style="visibility:hidden" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" not in result
        assert stripped[0].reason == "hidden_image"

    def test_visible_image_not_stripped(self) -> None:
        html = '<img src="cid:chart" style="display:block" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert "<img" in result
        assert len(stripped) == 0


class TestTrackingPixelCidSafeguard:
    """CID references are not stripped by tracking pixel detection alone."""

    def test_cid_with_small_dimensions_preserved(self) -> None:
        html = '<img src="cid:spacer1" width="1" height="1" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=True)
        assert 'src="cid:spacer1"' in result
        assert len(stripped) == 0


class TestFlagIndependence:
    """Flags operate independently."""

    def test_tracking_off_does_not_strip(self) -> None:
        html = '<img src="https://t.example.com/pixel.gif" width="1" height="1" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=False)
        assert "<img" in result
        assert len(stripped) == 0

    def test_both_off_returns_unchanged(self) -> None:
        html = '<div><img src="https://t.example.com/pixel.gif" width="1" height="1" /></div>'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=False)
        # filter_images short-circuits and returns input unchanged when both flags are off.
        assert result == html
        assert len(stripped) == 0


class TestSignatureLayer1GmailWrapper:
    """Layer 1: Gmail structural wrappers."""

    def test_gmail_data_smartmail(self) -> None:
        html = (
            '<div>Body content</div>'
            '<div data-smartmail="gmail_signature">'
            '<img src="cid:logo.png" alt="Company Logo" />'
            '</div>'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "logo.png" not in result
        assert len(stripped) == 1
        assert stripped[0].category == StrippedImageCategory.SIGNATURE_IMAGE
        assert stripped[0].reason == "gmail_signature_wrapper"

    def test_gmail_class(self) -> None:
        html = (
            '<div class="gmail_signature">'
            '<img src="cid:banner.jpg" />'
            '</div>'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "banner.jpg" not in result
        assert stripped[0].reason == "gmail_signature_wrapper"

    def test_gmail_body_images_preserved(self) -> None:
        html = (
            '<div><img src="cid:photo.jpg" alt="Meeting notes" /></div>'
            '<div data-smartmail="gmail_signature">'
            '<img src="cid:logo.png" />'
            '</div>'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert 'src="cid:photo.jpg"' in result
        assert "logo.png" not in result
        assert len(stripped) == 1


class TestSignatureLayer1ThunderbirdWrapper:
    """Layer 1: Thunderbird structural wrappers."""

    def test_moz_signature(self) -> None:
        html = '<div class="moz-signature"><img src="cid:sig.png" /></div>'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "sig.png" not in result
        assert stripped[0].reason == "thunderbird_signature_wrapper"

    def test_moz_txt_sig(self) -> None:
        html = '<div class="moz-txt-sig"><img src="cid:icon.gif" /></div>'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "icon.gif" not in result


class TestSignatureLayer1AppleMailWrapper:
    """Layer 1: Apple Mail structural wrappers."""

    def test_apple_string_attachment(self) -> None:
        html = '<span class="Apple-string-attachment"><img src="cid:apple-logo" /></span>'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "apple-logo" not in result
        assert stripped[0].reason == "apple_mail_signature_wrapper"


class TestSignatureLayer2GmailProxyUrl:
    """Layer 2: Gmail mail-sig proxy URL."""

    def test_mail_sig_url(self) -> None:
        html = '<img src="https://ci3.googleusercontent.com/mail-sig/abc123" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" not in result
        assert stripped[0].reason == "gmail_mail_sig_url"

    def test_gmail_proxy_url_preserved(self) -> None:
        """Non-signature Gmail proxy images should be preserved."""
        html = '<img src="https://ci3.googleusercontent.com/proxy/abc123" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" in result
        assert len(stripped) == 0


class TestSignatureLayer3FilenamePatterns:
    """Layer 3: Filename pattern matching."""

    def test_logo_in_src(self) -> None:
        html = '<img src="cid:company-logo.png" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" not in result
        assert stripped[0].reason == "filename_pattern:logo"

    def test_facebook_in_filename(self) -> None:
        html = '<img src="cid:facebook-icon.png" alt="Facebook" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" not in result
        assert "filename_pattern:" in stripped[0].reason

    def test_linkedin_in_alt(self) -> None:
        html = '<img src="cid:img001" alt="linkedin" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" not in result

    def test_spacer_in_filename(self) -> None:
        html = '<img src="cid:spacer.gif" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" not in result

    def test_generic_filename_preserved(self) -> None:
        """Outlook's image001.png should NOT be matched by filename patterns."""
        html = '<img src="cid:image001.png" alt="Screenshot" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" in result
        assert len(stripped) == 0

    def test_case_insensitive(self) -> None:
        html = '<img src="cid:COMPANY-LOGO.PNG" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "<img" not in result

    def test_signature_flag_off_preserves_all(self) -> None:
        html = '<img src="cid:company-logo.png" />'
        result, stripped = filter_images(html, strip_signature_images=False, strip_tracking_pixels=False)
        assert "<img" in result
        assert len(stripped) == 0


class TestSignatureLayer4StructuralBoundary:
    """Layer 4: Images that are subsequent siblings of a Layer 1 wrapper."""

    def test_sibling_img_after_gmail_wrapper(self) -> None:
        html = (
            '<div><img src="cid:body-photo.jpg" /></div>'
            '<div data-smartmail="gmail_signature"><p>John Doe</p></div>'
            '<img src="cid:social-row.png" />'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert 'src="cid:body-photo.jpg"' in result
        assert "social-row.png" not in result
        refs = {s.reference for s in stripped}
        assert "cid:social-row.png" in refs

    def test_no_boundary_extension_without_wrapper(self) -> None:
        """Layer 4 only fires when a Layer 1 wrapper is present."""
        html = '<div>Body</div><img src="cid:photo.jpg" />'
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert 'src="cid:photo.jpg"' in result
        assert len(stripped) == 0

    def test_multiple_sibling_imgs_after_wrapper(self) -> None:
        html = (
            '<div class="moz-signature"><p>--Jane</p></div>'
            '<img src="cid:fb.png" />'
            '<img src="cid:tw.png" />'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "fb.png" not in result
        assert "tw.png" not in result
        boundary_stripped = [s for s in stripped if s.reason == "structural_boundary_extension"]
        assert len(boundary_stripped) == 2


class TestSignatureLayer4BoundaryStoppingCondition:
    """Regression: boundary extension must stop at block-level content elements."""

    def test_stops_at_block_element_with_text_content(self) -> None:
        """Content images inside a block sibling with text should NOT be stripped."""
        html = (
            '<div data-smartmail="gmail_signature"><p>John Doe</p></div>'
            '<div><p>Important content below</p><img src="cid:chart.png" alt="Chart" /></div>'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert 'src="cid:chart.png"' in result
        boundary_stripped = [s for s in stripped if s.reason == "structural_boundary_extension"]
        assert len(boundary_stripped) == 0

    def test_bare_img_siblings_still_stripped(self) -> None:
        """Bare <img> siblings immediately after signature wrapper should still be stripped."""
        html = (
            '<div class="gmail_signature"><p>John Doe</p></div>'
            '<img src="cid:spacer.gif" />'
            '<div><p>Real content here</p></div>'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "spacer.gif" not in result
        assert 'Real content here' in result
        boundary_stripped = [s for s in stripped if s.reason == "structural_boundary_extension"]
        assert len(boundary_stripped) == 1

    def test_image_only_block_sibling_still_stripped(self) -> None:
        """A block sibling with only images and no text is still signature-adjacent."""
        html = (
            '<div data-smartmail="gmail_signature"><p>John Doe</p></div>'
            '<div><img src="cid:decoration.png" /></div>'
        )
        result, stripped = filter_images(html, strip_signature_images=True, strip_tracking_pixels=False)
        assert "decoration.png" not in result
        boundary_stripped = [s for s in stripped if s.reason == "structural_boundary_extension"]
        assert len(boundary_stripped) == 1


class TestPipelineIntegration:
    """filter_images integrates correctly with _build_rendered_markdown."""

    def test_stripped_images_in_diagnostics(self, tmp_path) -> None:
        """Stripped image metadata appears in diagnostics dict."""
        from dead_letter.core._pipeline import convert_to_bundle_with_diagnostics
        from dead_letter.core.types import ConvertOptions

        eml_content = (
            "From: test@example.com\n"
            "Subject: Test\n"
            "MIME-Version: 1.0\n"
            "Content-Type: text/html; charset=utf-8\n"
            "\n"
            '<html><body><p>Hello</p>'
            '<img src="https://t.example.com/pixel.gif" width="1" height="1" />'
            '</body></html>'
        )
        eml_path = tmp_path / "test.eml"
        eml_path.write_text(eml_content)
        bundle_root = tmp_path / "bundles"
        bundle_root.mkdir()

        opts = ConvertOptions(strip_tracking_pixels=True)
        result, diagnostics = convert_to_bundle_with_diagnostics(
            eml_path, bundle_root=bundle_root, options=opts,
        )
        assert result.success
        assert diagnostics is not None
        assert "stripped_images" in diagnostics
        assert len(diagnostics["stripped_images"]) == 1
        assert diagnostics["stripped_images"][0]["category"] == "tracking_pixel"

    def test_stripped_cid_not_in_markdown_output(self, tmp_path) -> None:
        """Stripped CID images do not appear as broken links in markdown output."""
        from dead_letter.core._pipeline import convert_to_bundle_with_diagnostics
        from dead_letter.core.types import ConvertOptions

        eml_content = (
            "From: test@example.com\n"
            "Subject: CID cleanup test\n"
            "MIME-Version: 1.0\n"
            "Content-Type: text/html; charset=utf-8\n"
            "\n"
            '<html><body><p>Hello</p>'
            '<div data-smartmail="gmail_signature">'
            '<img src="cid:company-logo.png" alt="Logo" />'
            '</div></body></html>'
        )
        eml_path = tmp_path / "test.eml"
        eml_path.write_text(eml_content)
        bundle_root = tmp_path / "bundles"
        bundle_root.mkdir()

        opts = ConvertOptions(strip_signature_images=True)
        result, _diagnostics = convert_to_bundle_with_diagnostics(
            eml_path, bundle_root=bundle_root, options=opts,
        )
        assert result.success
        md_content = result.markdown.read_text()
        assert "cid:company-logo.png" not in md_content
