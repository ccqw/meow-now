"""Headless Chromium smoke test of the single-page UI against a live local server.

Run with `pytest --browser-smoke tests/browser` (see the README). Chromium's fake
microphone plays a repeating beep, which the analysis hears as voiced syllables.
"""

import re
import socket
import threading
import time

import numpy as np
import pytest
import uvicorn
from playwright.sync_api import Page, expect

from meow_now.audio import encode_wav
from meow_now.web import Settings, create_app

# Checked in the page rather than with expect(), so a failure does not print a huge data URL.
WAV_SRC = "a => a.src.startsWith('data:audio/wav;base64,')"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
        ],
    }


@pytest.fixture(scope="session")
def server_url():
    settings = Settings(ollama_url=None, ollama_model="x", max_upload_bytes=4 * 1024 * 1024)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if time.monotonic() > deadline or not thread.is_alive():
                    raise RuntimeError("uvicorn did not start")
                time.sleep(0.05)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)


# Records every CSP violation the page reports, including ones that only show up as an event.
CSP_RECORDER = """
window.__cspViolations = [];
document.addEventListener("securitypolicyviolation", (e) => {
  window.__cspViolations.push(e.violatedDirective + " " + (e.blockedURI || "inline"));
});
"""


def _csp_violations(page: Page, console: list[str]) -> list[str]:
    reported = page.evaluate("window.__cspViolations")
    return reported + [m for m in console if "Content Security Policy" in m]


@pytest.fixture
def watched(page: Page, server_url):
    """The page, loaded, with page errors and CSP console messages collected."""
    errors, console = [], []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: console.append(msg.text))
    page.add_init_script(CSP_RECORDER)
    page.goto(server_url + "/")
    return page, errors, console


@pytest.fixture
def home(watched):
    page, errors, console = watched
    yield page
    assert errors == []
    assert _csp_violations(page, console) == []


def test_page_loads_and_lists_cats(home: Page):
    expect(home).to_have_title("Meow Now")
    cats = home.locator("#cats button.cat")
    expect(cats).to_have_count(4)
    expect(cats.first).to_have_attribute("aria-pressed", "true")
    expect(home.locator("#cat-status")).to_have_text("")


def test_record_then_stop_shows_mockery(home: Page):
    rec = home.locator("#rec")
    rec.click()
    expect(rec).to_have_text("Done")
    expect(home.locator("#mockery")).to_have_class("card recording")
    home.wait_for_timeout(3000)
    rec.click()
    expect(rec).to_have_text("Start talking")
    status = home.locator("#mock-status")
    expect(status).to_have_text(
        re.compile(r"^\d+ meows for [\d.]+ seconds of you\.$"), timeout=15000
    )
    assert int(status.inner_text().split()[0]) >= 1
    expect(home.locator("#mock-audio")).to_be_visible()
    assert home.locator("#mock-audio").evaluate(WAV_SRC)
    expect(home.locator("#bars i")).to_have_count(48)
    assert home.locator("#bars i").evaluate_all(
        "bars => bars.some((b) => b.style.height !== '2px')"
    )


def test_silent_recording_shows_the_caption(home: Page):
    # The fake microphone always beeps, so swap the upload for a second of silence.
    silence = encode_wav(np.zeros(16_000, dtype=np.float32), 16_000)
    home.route(
        "**/api/mockery*", lambda route: route.fulfill(response=route.fetch(post_data=silence))
    )
    rec = home.locator("#rec")
    rec.click()
    expect(rec).to_have_text("Done")
    home.wait_for_timeout(1000)
    rec.click()
    status = home.locator("#mock-status")
    expect(status).to_have_text("The cat waited. So did you.", timeout=15000)
    expect(status).not_to_have_class(re.compile("error"))
    expect(home.locator("#mock-audio")).to_be_visible()
    assert home.locator("#mock-audio").evaluate(WAV_SRC)


def test_talkative_cat_plays_with_subtitles(home: Page):
    second = home.locator("#cats button.cat").nth(1)
    name = second.locator("strong").inner_text()
    second.click()
    expect(second).to_have_attribute("aria-pressed", "true")
    home.locator("#speak").click()
    subtitle = home.locator("#subtitle")
    expect(subtitle.locator(".who")).to_have_text(name, timeout=15000)
    assert subtitle.locator(".w").count() >= 3
    audio = home.locator("#cat-audio")
    expect(audio).to_be_visible()
    assert audio.evaluate(WAV_SRC)
    # Playback drives the word highlighting, so a lit word means the audio really played.
    expect(subtitle.locator(".w.said").first).to_be_visible(timeout=15000)
    expect(home.locator("#speak")).to_be_enabled()


def test_csp_is_enforced_and_violations_are_caught(watched):
    page, _, console = watched
    csp = page.request.get(page.url).headers["content-security-policy"]
    assert "'nonce-" in csp and "unsafe-inline" not in csp
    # An inline script without the page's nonce must be refused, and the check above must see it.
    page.evaluate(
        """() => {
          const s = document.createElement("script");
          s.textContent = "window.__ran = true";
          document.body.append(s);
        }"""
    )
    expect(page.locator("body")).to_be_visible()
    page.wait_for_function("window.__cspViolations.length > 0")
    assert page.evaluate("window.__ran === undefined")
    assert any("script-src" in v for v in _csp_violations(page, console))
