"""Headless Chromium smoke test of the single-page UI against a live local server.

Run with `pytest --browser-smoke tests/browser` (see the README). Chromium's fake
microphone plays a repeating beep, which the analysis hears as voiced syllables.
"""

import re
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import Page, expect

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
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()


@pytest.fixture
def home(page: Page, server_url):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(server_url + "/")
    yield page
    assert errors == []


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
