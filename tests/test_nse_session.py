"""
NSE session bootstrap regression tests.

Prevents regressions around the session-cookie handshake that NSE requires.
These tests run without network access — all HTTP calls are mocked.

Key invariants tested:
1. _get_session() hits the homepage BEFORE any data download
2. Bootstrap failure (non-200 homepage) is raised, not silently swallowed
3. A non-zip response from the download endpoint is rejected (PK magic-byte check)
4. The session object is reused across multiple downloads (cookies persist)

Background: NSE returns 403 to requests without a session cookie obtained
from the homepage. A previous version of the code had raise_for_status() in
the wrong place, which was caught during M0.1 live validation.
"""

import io
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from ingestion.nse_source import NSEDataSource, NSEDownloadError


class TestSessionBootstrap:
    """Verify the homepage-first session bootstrap behavior."""

    @patch("ingestion.nse_source.requests.Session")
    def test_session_hits_homepage_before_download(self, MockSession):
        """_get_session() must GET the homepage to establish cookies."""
        mock_session = MockSession.return_value
        mock_home_resp = MagicMock()
        mock_home_resp.status_code = 200
        mock_home_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_home_resp

        source = NSEDataSource(raw_dir=Path("/tmp/test"))
        session = source._get_session()

        # The first call to session.get() should be the homepage
        calls = mock_session.get.call_args_list
        assert len(calls) >= 1, "session.get() was never called"
        first_url = calls[0][0][0]
        assert first_url == NSEDataSource.HOME_URL, (
            f"First GET should be homepage ({NSEDataSource.HOME_URL}), "
            f"got: {first_url}"
        )
        # raise_for_status should be called on the homepage response
        mock_home_resp.raise_for_status.assert_called_once()

    @patch("ingestion.nse_source.requests.Session")
    def test_bootstrap_failure_raises(self, MockSession):
        """If the homepage returns a non-200 status, _get_session() must raise."""
        mock_session = MockSession.return_value
        mock_home_resp = MagicMock()
        mock_home_resp.status_code = 403
        mock_home_resp.raise_for_status.side_effect = Exception("403 Forbidden")
        mock_session.get.return_value = mock_home_resp

        source = NSEDataSource(raw_dir=Path("/tmp/test"))

        with pytest.raises(Exception, match="403"):
            source._get_session()

    @patch("ingestion.nse_source.requests.Session")
    def test_non_zip_response_rejected(self, MockSession):
        """A download that returns HTML instead of a zip must be caught."""
        mock_session = MockSession.return_value

        # Homepage succeeds
        mock_home_resp = MagicMock()
        mock_home_resp.status_code = 200
        mock_home_resp.raise_for_status = MagicMock()

        # Download returns HTML (not a zip)
        mock_dl_resp = MagicMock()
        mock_dl_resp.status_code = 200
        mock_dl_resp.content = b"<html>Access Denied</html>"
        mock_dl_resp.raise_for_status = MagicMock()

        mock_session.get.side_effect = [mock_home_resp, mock_dl_resp]

        source = NSEDataSource(raw_dir=Path("/tmp/test_nonzip"))
        trading_date = date(2026, 1, 15)

        with pytest.raises(NSEDownloadError, match="not a valid zip"):
            source.download(trading_date)

    @patch("ingestion.nse_source.requests.Session")
    def test_session_reused_across_downloads(self, MockSession):
        """The session (and its cookies) must be reused, not recreated."""
        mock_session = MockSession.return_value

        mock_home_resp = MagicMock()
        mock_home_resp.status_code = 200
        mock_home_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_home_resp

        source = NSEDataSource(raw_dir=Path("/tmp/test_reuse"))

        session1 = source._get_session()
        session2 = source._get_session()

        assert session1 is session2, "Session should be cached, not recreated"
        # Homepage should only be hit once
        home_calls = [
            c for c in mock_session.get.call_args_list
            if c[0][0] == NSEDataSource.HOME_URL
        ]
        assert len(home_calls) == 1, (
            f"Homepage should be called exactly once, called {len(home_calls)} times"
        )

    @patch("ingestion.nse_source.requests.Session")
    def test_404_raises_descriptive_error(self, MockSession):
        """A 404 from the download endpoint should give a useful error message."""
        mock_session = MockSession.return_value

        mock_home_resp = MagicMock()
        mock_home_resp.status_code = 200
        mock_home_resp.raise_for_status = MagicMock()

        mock_dl_resp = MagicMock()
        mock_dl_resp.status_code = 404
        mock_dl_resp.content = b""

        mock_session.get.side_effect = [mock_home_resp, mock_dl_resp]

        source = NSEDataSource(raw_dir=Path("/tmp/test_404"))
        trading_date = date(2026, 1, 15)

        with pytest.raises(NSEDownloadError, match="No bhavcopy found"):
            source.download(trading_date)
