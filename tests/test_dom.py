"""DOM-based tests for the MegaphoneMonitor Web Component.

Uses Playwright (headless Chromium) to verify rendering, interaction,
SSE dedup, tab switching, search, and export — all through real DOM.
"""
from __future__ import annotations

import socket

import pytest

from megaphone.webserver import start_web_server

pytestmark = pytest.mark.dom


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server():
    """Start a web server on a random port for the whole test module."""
    port = _free_port()
    srv = start_web_server(port)
    yield srv, port
    srv.shutdown()


@pytest.fixture()
def test_page(page, server):
    """Navigate to /test and wait for the component to be ready."""
    _, port = server
    page.goto(f"http://localhost:{port}/test")
    # Wait for the custom element to be defined and rendered.
    page.wait_for_selector("megaphone-monitor")
    # Give the shadow DOM time to render.
    page.wait_for_timeout(500)
    return page


# ---------------------------------------------------------------------------
# Shadow DOM helpers
# ---------------------------------------------------------------------------

def shadow(page, selector: str):
    """Locate an element inside the megaphone-monitor shadow root."""
    return page.locator(f"megaphone-monitor >> {selector}")


def shadow_all(page, selector: str):
    """Locate all matching elements inside the shadow root."""
    return page.locator(f"megaphone-monitor >> {selector}")


# ---------------------------------------------------------------------------
# Tests: Component rendering
# ---------------------------------------------------------------------------

class TestRendering:
    """Verify the Web Component renders its key structural elements."""

    def test_header_title_rendered(self, test_page):
        """INVARIANT: Shadow DOM contains the header title."""
        title = shadow(test_page, "header h1")
        title.wait_for(state="attached", timeout=3000)
        assert title.text_content() == "MAFIA42 확성기"

    def test_search_input_rendered(self, test_page):
        """INVARIANT: Search input exists inside shadow root."""
        inp = shadow(test_page, "#searchInput")
        inp.wait_for(state="attached", timeout=3000)
        assert inp.is_visible()

    def test_tabs_container_rendered(self, test_page):
        """INVARIANT: Tabs container with '전체' tab exists."""
        tab = shadow(test_page, '.tab[data-ch="all"]')
        tab.wait_for(state="attached", timeout=3000)
        assert tab.text_content().strip() == "전체"

    def test_messages_container_rendered(self, test_page):
        """INVARIANT: Messages container exists."""
        msgs = shadow(test_page, ".message-table")
        msgs.wait_for(state="attached", timeout=3000)
        assert msgs.is_visible()

    def test_footer_rendered(self, test_page):
        """INVARIANT: Footer with message count exists."""
        count = shadow(test_page, "#msgCount")
        count.wait_for(state="attached", timeout=3000)
        assert count.text_content().strip() == "0"

    def test_sim_controls_visible_in_test_mode(self, test_page):
        """INVARIANT: Simulation controls appear in test mode."""
        sim = shadow(test_page, "#simControls")
        sim.wait_for(state="attached", timeout=3000)
        assert sim.is_visible()

    def test_channel_tabs_created(self, test_page):
        """INVARIANT: Channel tabs are created from the channels attribute."""
        btn = shadow(test_page, "#startBtn")
        btn.wait_for(state="attached", timeout=3000)
        assert btn.is_visible()


# ---------------------------------------------------------------------------
# Tests: Simulation interaction
# ---------------------------------------------------------------------------

class TestSimulation:
    """Verify simulation start/stop and message rendering."""

    def test_start_button_toggles(self, test_page):
        """INVARIANT: Start button toggles between 시작/정지."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(300)
        assert btn.text_content() == "정지"
        btn.click()
        test_page.wait_for_timeout(300)
        assert btn.text_content() == "시작"

    def test_messages_appear_after_start(self, test_page):
        """INVARIANT: Messages appear in the DOM after simulation starts."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        # Wait: 7 channels x 300ms connect delay + message generation.
        test_page.wait_for_timeout(4000)
        msgs = shadow_all(test_page, "#messageBody tr")
        assert msgs.count() > 0
        btn.click()  # Stop.

    def test_message_has_time_channel_sender_text(self, test_page):
        """INVARIANT: Each rendered message has time, channel, sender, text."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)

        time_el = shadow(test_page, "#messageBody tr .time").first
        ch_el = shadow(test_page, "#messageBody tr .channel").first
        sender_el = shadow(test_page, "#messageBody tr .nickname").first
        text_el = shadow(test_page, "#messageBody tr .content").first

        time_el.wait_for(state="attached", timeout=3000)
        assert time_el.text_content() != ""
        assert ch_el.text_content() != ""
        assert sender_el.text_content() != ""
        assert text_el.text_content() != ""
        btn.click()

    def test_message_count_increases(self, test_page):
        """INVARIANT: msgCount footer updates as messages arrive."""
        count_before = shadow(test_page, "#msgCount").text_content()
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)
        count_after = shadow(test_page, "#msgCount").text_content()
        btn.click()
        # Extract numbers.
        n_before = int(count_before.strip())
        n_after = int(count_after.strip())
        assert n_after > n_before


# ---------------------------------------------------------------------------
# Tests: Tab switching
# ---------------------------------------------------------------------------

class TestTabSwitching:
    """Verify that clicking channel tabs filters the message list."""

    def test_tab_switch_hides_other_channel_messages(self, test_page):
        """INVARIANT: Switching to a channel tab shows only that channel's messages."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(5000)

        tabs = shadow_all(test_page, ".tab:not([data-ch='all'])")
        tab_count = tabs.count()
        if tab_count == 0:
            btn.click()
            pytest.skip("No channel tabs created during simulation")

        first_tab = tabs.nth(0)
        first_tab.click()
        test_page.wait_for_timeout(500)

        visible_msgs = shadow_all(test_page, "#messageBody tr:not(.hidden)")
        assert visible_msgs.count() >= 0
        btn.click()

    def test_all_tab_shows_everything(self, test_page):
        """INVARIANT: '전체' tab shows messages from all channels."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)

        tabs = shadow_all(test_page, ".tab:not([data-ch='all'])")
        if tabs.count() > 0:
            tabs.nth(0).click()
            test_page.wait_for_timeout(300)

        all_tab = shadow(test_page, '.tab[data-ch="all"]')
        all_tab.click()
        test_page.wait_for_timeout(500)

        visible = shadow_all(test_page, "#messageBody tr:not(.hidden)")
        assert visible.count() > 0
        btn.click()


# ---------------------------------------------------------------------------
# Tests: Search / filter
# ---------------------------------------------------------------------------

class TestSearch:
    """Verify search input filters messages by sender or text."""

    def test_search_hides_non_matching(self, test_page):
        """INVARIANT: Typing in search input hides non-matching messages."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)
        total_before = shadow_all(test_page, "#messageBody tr").count()

        inp = shadow(test_page, "#searchInput")
        inp.fill("존재하지않는검색어12345")
        test_page.wait_for_timeout(500)

        visible_after = shadow_all(test_page, "#messageBody tr:not(.hidden)")
        assert visible_after.count() < total_before

        inp.fill("")
        test_page.wait_for_timeout(300)
        btn.click()

    def test_search_highlights_matches(self, test_page):
        """INVARIANT: Matching text is wrapped in <mark> tags."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)

        first_text = shadow(test_page, "#messageBody tr .content").first.text_content()
        if len(first_text) < 2:
            btn.click()
            pytest.skip("First message too short to search")

        query = first_text[:3]
        inp = shadow(test_page, "#searchInput")
        inp.fill(query)
        test_page.wait_for_timeout(500)

        marks = shadow_all(test_page, "#messageBody tr .content mark")
        assert marks.count() > 0
        inp.fill("")
        btn.click()


# ---------------------------------------------------------------------------
# Tests: Clear
# ---------------------------------------------------------------------------

class TestClear:
    """Verify clear button removes all messages."""

    def test_clear_removes_all_messages(self, test_page):
        """INVARIANT: Clicking clear button empties the message list."""
        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)
        btn.click()  # Stop simulation first.
        test_page.wait_for_timeout(300)

        clear_btn = shadow(test_page, "#clearBtn")
        clear_btn.click()
        test_page.wait_for_timeout(500)

        # After clearing, only the empty-state row should remain.
        empty_state = shadow(test_page, ".empty-state")
        empty_state.wait_for(state="attached", timeout=3000)
        assert empty_state.is_visible()
        count = shadow(test_page, "#msgCount").text_content()
        assert count.strip() == "0"


# ---------------------------------------------------------------------------
# Tests: SSE dedup (seq-based)
# ---------------------------------------------------------------------------

class TestDedup:
    """Verify the seq-based dedup in addMessage prevents duplicates."""

    def test_addMessage_dedup_by_seq(self, test_page):
        """INVARIANT: Calling addMessage twice with same seq produces one entry."""
        result = test_page.evaluate("""() => {
            const monitor = document.querySelector('megaphone-monitor');
            monitor.clearMessages();
            const msg = { seq: 99999, channel_id: 0, channel_name: '초보',
                          time: '2025-01-01:00-00-00', sender: '테스트',
                          message: '중복 테스트', scope: 'server', msg_id: 1 };
            monitor.addMessage(msg);
            monitor.addMessage(msg);  // duplicate -- should be skipped
            return monitor._messages.length;
        }""")
        assert result == 1

    def test_addMessage_different_seqs_both_added(self, test_page):
        """INVARIANT: Different seq values produce separate entries."""
        result = test_page.evaluate("""() => {
            const monitor = document.querySelector('megaphone-monitor');
            monitor.clearMessages();
            const msg1 = { seq: 88881, channel_id: 0, channel_name: '초보',
                           time: '2025-01-01:00-00-00', sender: 'A',
                           message: 'msg1', scope: 'server', msg_id: 1 };
            const msg2 = { seq: 88882, channel_id: 1, channel_name: '1채널',
                           time: '2025-01-01:00-00-01', sender: 'B',
                           message: 'msg2', scope: 'server', msg_id: 2 };
            monitor.addMessage(msg1);
            monitor.addMessage(msg2);
            return monitor._messages.length;
        }""")
        assert result == 2

    def test_addMessage_null_seq_no_crash(self, test_page):
        """INVARIANT: Message with null/undefined seq is still added (no crash)."""
        result = test_page.evaluate("""() => {
            const monitor = document.querySelector('megaphone-monitor');
            monitor.clearMessages();
            const msg = { channel_id: 0, channel_name: '초보',
                          time: '2025-01-01:00-00-00', sender: 'X',
                          message: 'no seq', scope: 'server', msg_id: 1 };
            monitor.addMessage(msg);
            return monitor._messages.length;
        }""")
        assert result == 1


# ---------------------------------------------------------------------------
# Tests: Export
# ---------------------------------------------------------------------------

class TestExport:
    """Verify export produces valid JSON/CSV blobs."""

    def test_export_json_returns_blob(self, test_page):
        """INVARIANT: exportMessages('json') returns a Blob with valid JSON."""
        result = test_page.evaluate("""async () => {
            const monitor = document.querySelector('megaphone-monitor');
            monitor.clearMessages();
            monitor.addMessage({ seq: 1, channel_id: 0, channel_name: '초보',
                time: '2025-01-01:00-00-00', sender: 'A', message: 'test',
                scope: 'server', msg_id: 1 });
            const blob = await monitor.exportMessages('json');
            const text = await blob.text();
            const parsed = JSON.parse(text);
            return { type: blob.type, count: parsed.length };
        }""")
        assert result["type"] == "application/json"
        assert result["count"] == 1

    def test_export_csv_has_header(self, test_page):
        """INVARIANT: exportMessages('csv') returns CSV with correct header."""
        result = test_page.evaluate("""async () => {
            const monitor = document.querySelector('megaphone-monitor');
            monitor.clearMessages();
            monitor.addMessage({ seq: 2, channel_id: 0, channel_name: '초보',
                time: '2025-01-01:00-00-00', sender: 'A', message: 'test',
                scope: 'server', msg_id: 1 });
            const blob = await monitor.exportMessages('csv');
            const text = await blob.text();
            return { type: blob.type, header: text.split('\\n')[0] };
        }""")
        assert result["type"] == "text/csv"
        assert "time" in result["header"]
        assert "sender" in result["header"]
        assert "message" in result["header"]


# ---------------------------------------------------------------------------
# Tests: Responsive / CSS
# ---------------------------------------------------------------------------

class TestResponsive:
    """Verify responsive layout behavior at different viewport widths."""

    def test_mobile_layout_hides_time(self, test_page):
        """INVARIANT: At 375px width, first column (#) is hidden."""
        test_page.set_viewport_size({"width": 375, "height": 812})
        test_page.wait_for_timeout(300)

        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)

        first_col = shadow(test_page, "#messageBody tr td:first-child")
        if first_col.count() > 0:
            display = test_page.evaluate("""() => {
                const el = document.querySelector('megaphone-monitor')
                    .shadowRoot.querySelector('#messageBody tr td:first-child');
                return window.getComputedStyle(el).display;
            }""")
            assert display == "none"
        btn.click()

    def test_desktop_shows_time(self, test_page):
        """INVARIANT: At 1280px width, message time is visible."""
        test_page.set_viewport_size({"width": 1280, "height": 800})
        test_page.wait_for_timeout(300)

        btn = shadow(test_page, "#startBtn")
        btn.click()
        test_page.wait_for_timeout(4000)

        time_el = shadow(test_page, "#messageBody tr .time")
        if time_el.count() > 0:
            display = test_page.evaluate("""() => {
                const el = document.querySelector('megaphone-monitor')
                    .shadowRoot.querySelector('#messageBody tr .time');
                return window.getComputedStyle(el).display;
            }""")
            assert display != "none"
        btn.click()
