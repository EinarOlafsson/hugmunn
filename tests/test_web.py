"""Web tool tests.

The SSRF guard is the part that must not regress: without it, ``web_fetch``
turns a model-supplied string into a request against the local network.
"""

from __future__ import annotations

import pytest

from localagent.core import web


class TestSSRFGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/",
            "http://localhost:8080/",
            "http://0.0.0.0/",
            "http://10.0.0.5/",
            "http://192.168.1.1/admin",
            "http://172.16.4.4/",
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://[::1]/",
        ],
    )
    def test_private_and_loopback_rejected(self, url):
        with pytest.raises(web.WebError):
            web._assert_public(url)

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x/"])
    def test_non_http_schemes_rejected(self, url):
        with pytest.raises(web.WebError, match="only http/https"):
            web._assert_public(url)

    def test_unresolvable_host_rejected(self):
        with pytest.raises(web.WebError, match="could not resolve"):
            web._assert_public("http://nonexistent.invalid./")

    def test_missing_hostname_rejected(self):
        with pytest.raises(web.WebError):
            web._assert_public("http:///just-a-path")

    def test_public_host_allowed(self):
        assert web._assert_public("https://example.com/") == "https://example.com/"


class TestHtmlToText:
    def test_tags_stripped(self):
        assert "Hello" in web.html_to_text("<p><b>Hello</b></p>")
        assert "<b>" not in web.html_to_text("<p><b>Hello</b></p>")

    def test_script_and_style_dropped(self):
        out = web.html_to_text("<p>keep</p><script>var secret=1</script><style>a{}</style>")
        assert "keep" in out
        assert "secret" not in out and "a{}" not in out

    def test_entities_decoded(self):
        assert "AT&T" in web.html_to_text("<p>AT&amp;T</p>")

    def test_block_elements_become_newlines(self):
        assert "\n" in web.html_to_text("<p>one</p><p>two</p>")

    def test_runs_of_blank_lines_collapsed(self):
        assert "\n\n\n" not in web.html_to_text("<div>a</div>" + "<br>" * 12 + "<div>b</div>")

    def test_plain_text_survives(self):
        assert web.html_to_text("just words") == "just words"


class TestClipping:
    def test_short_text_unchanged(self):
        assert web._clip("abc", 100) == "abc"

    def test_long_text_truncated_with_marker(self):
        out = web._clip("x" * 500, 100)
        assert len(out) < 300
        assert "truncated" in out

    def test_boundary_exact_length_unchanged(self):
        assert web._clip("x" * 100, 100) == "x" * 100


class TestToolRegistration:
    def test_web_tools_registered(self):
        from localagent.core import tools

        assert "web_search" in tools.BY_NAME
        assert "web_fetch" in tools.BY_NAME

    def test_web_tools_do_not_require_approval(self):
        from localagent.core import tools

        assert not tools.BY_NAME["web_fetch"].requires_approval
        assert not tools.BY_NAME["web_search"].requires_approval

    def test_ssrf_error_is_recoverable_not_raised(self):
        """A blocked URL must come back as a string the model can react to."""
        from localagent.core import tools

        out = tools.execute("web_fetch", {"url": "http://127.0.0.1/"}, "/tmp")
        assert out.startswith("Error:")
        assert "non-public" in out


class TestPdfLinkDetection:
    def test_extension_links_matched(self):
        found = web._PDF_HREF.findall('<a href="/docs/paper.pdf">x</a>')
        assert found == ["/docs/paper.pdf"]

    def test_extensionless_pdf_route_matched(self):
        """arXiv links look like /pdf/2401.12345 with no extension."""
        found = web._PDF_HREF.findall('<a href="/pdf/2608.07344">x</a>')
        assert found == ["/pdf/2608.07344"]

    def test_query_string_tolerated(self):
        found = web._PDF_HREF.findall('<a href="/get.pdf?id=7">x</a>')
        assert found == ["/get.pdf?id=7"]

    def test_non_pdf_ignored(self):
        assert web._PDF_HREF.findall('<a href="/page.html">x</a>') == []


class TestDownloadDestination:
    def test_traversal_rejected_before_any_request(self, tmp_path):
        """Must fail on the path alone — not only when the page has PDF links."""
        from localagent.core import tools
        out = tools.execute(
            "download_pdfs",
            {"url": "https://example.com/", "subdir": "../../etc"},
            str(tmp_path),
        )
        assert out.startswith("Error:") and "outside the working directory" in out

    def test_absolute_destination_rejected(self, tmp_path):
        from localagent.core import tools
        out = tools.execute(
            "download_pdfs", {"url": "https://example.com/", "subdir": "/etc"}, str(tmp_path)
        )
        assert out.startswith("Error:")

    def test_download_pdfs_requires_approval(self):
        from localagent.core import tools
        assert tools.BY_NAME["download_pdfs"].requires_approval

    def test_image_search_registered(self):
        from localagent.core import tools
        assert "image_search" in tools.BY_NAME
