"""Headless Chromium smoke test of the single-page UI against a live local server.

Run with `pytest --browser-smoke tests/browser` (see the README). Chromium's fake
microphone plays a repeating beep, which the analysis hears as voiced syllables.
"""

import re
import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import uvicorn
from playwright.sync_api import Page, expect

from meow_now.audio import encode_wav
from meow_now.web import Settings, create_app

# Checked in the page rather than with expect(), so a failure does not print a huge data URL.
WAV_SRC = "a => a.src.startsWith('data:audio/wav;base64,')"
# Logs every value the stage cat's data-mouth takes into window.mouths.
MOUTH_LOG = """() => {
    window.mouths = [];
    const cat = document.getElementById("cat");
    new MutationObserver(() => window.mouths.push(cat.dataset.mouth))
        .observe(cat, { attributes: true, attributeFilter: ["data-mouth"] });
}"""


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
    # The stage cat wears the chosen cat's coat and idles with its mouth shut.
    stage = home.locator("#cat")
    expect(stage).to_have_attribute("data-coat", cats.first.get_attribute("data-id"))
    expect(stage).to_have_attribute("data-mouth", "closed")
    expect(home.locator("#play")).to_be_disabled()


def test_record_then_stop_shows_mockery(home: Page):
    rec = home.locator("#rec")
    rec.click()
    expect(rec).to_have_text("Done")
    expect(home.locator("#mockery")).to_have_class(re.compile(r"\brecording\b"))
    expect(home.locator("#cat")).to_have_attribute("data-state", "listening")
    # The live trace draws your voice while you talk.
    expect(home.locator("#trace-level")).to_have_attribute("d", re.compile(r"^M"), timeout=5000)
    home.wait_for_timeout(3000)
    # Each meow opens the jaw only briefly, so record every change rather than poll for one.
    home.evaluate(MOUTH_LOG)
    rec.click()
    expect(rec).to_have_text("Start talking")
    status = home.locator("#mock-status")
    expect(status).to_have_text(
        re.compile(r"^\d+ meows? for [\d.]+ seconds of you\.$"), timeout=15000
    )
    assert int(status.inner_text().split()[0]) >= 1
    assert home.locator("#mock-audio").evaluate(WAV_SRC)
    # The ribbon draws both pitch lines, one cat stroke per meow.
    meows = int(status.inner_text().split()[0])
    expect(home.locator("#ribbon-dim .cat-line")).to_have_count(meows)
    expect(home.locator("#ribbon-dim .you-line")).to_have_count(meows)
    expect(home.locator("#track")).to_have_attribute("data-mode", "mockery")
    # Mouth sync: the jaw opens on a meow during playback, then closes again.
    home.wait_for_function("() => window.mouths.includes('open')", timeout=15000)
    expect(home.locator("#cat")).to_have_attribute("data-mouth", "closed", timeout=15000)
    expect(home.locator("#subtitle .w")).to_have_count(meows)


def test_mockery_is_sent_in_the_chosen_cats_voice(home: Page):
    third = home.locator("#cats button.cat").nth(2)
    cat_id = third.get_attribute("data-id")
    third.click()
    expect(third).to_have_attribute("aria-pressed", "true")
    rec = home.locator("#rec")
    rec.click()
    expect(rec).to_have_text("Done")
    home.wait_for_timeout(1500)
    with home.expect_response(re.compile(r"/api/mockery")) as info:
        rec.click()
    response = info.value
    assert f"cat={cat_id}" in response.url
    assert response.json()["cat"]["id"] == cat_id
    expect(home.locator("#mock-status")).to_have_text(re.compile(r"meows? for"), timeout=15000)


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
    expect(home.locator("#play")).to_be_enabled()
    assert home.locator("#mock-audio").evaluate(WAV_SRC)
    # The caption sits in the speech bubble over the cat's one questioning mrrp.
    subtitle = home.locator("#subtitle")
    expect(subtitle.locator(".caption")).to_have_text("The cat waited. So did you.")
    expect(subtitle.locator(".w")).to_have_text(["Mrrp?"])
    expect(home.locator("#ribbon-dim .cat-line.reaction")).to_have_count(1)
    expect(home.locator("#ribbon-dim .you-line")).to_have_count(0)


def test_talkative_cat_plays_with_subtitles(home: Page):
    second = home.locator("#cats button.cat").nth(1)
    name = second.locator("strong").inner_text()
    second.click()
    expect(second).to_have_attribute("aria-pressed", "true")
    expect(home.locator("#cat")).to_have_attribute("data-coat", second.get_attribute("data-id"))
    home.evaluate(MOUTH_LOG)
    home.locator("#speak").click()
    subtitle = home.locator("#subtitle")
    expect(subtitle).to_have_attribute("data-kind", "monologue", timeout=15000)
    expect(subtitle.locator(".who")).to_have_text(name)
    assert subtitle.locator(".w").count() >= 3
    assert home.locator("#cat-audio").evaluate(WAV_SRC)
    # Playback drives the word highlighting, so a lit word means the audio really played.
    expect(subtitle.locator(".w.said").first).to_be_visible(timeout=15000)
    home.wait_for_function("() => window.mouths.includes('open')", timeout=15000)
    expect(home.locator("#ribbon-dim .word-tick")).to_have_count(subtitle.locator(".w").count())
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
    page.wait_for_function("() => window.__cspViolations.length > 0")
    assert page.evaluate("window.__ran === undefined")
    assert any("script-src" in v for v in _csp_violations(page, console))


def test_player_pauses_and_seeks_from_the_keyboard(home: Page):
    home.locator("#speak").click()
    play = home.locator("#play")
    expect(play).to_have_attribute("data-state", "playing", timeout=15000)
    play.focus()
    home.keyboard.press("Space")
    expect(play).to_have_attribute("data-state", "paused")
    expect(play).to_have_attribute("aria-label", "Play")
    assert home.locator("#cat-audio").evaluate("a => a.paused")
    track = home.locator("#track")
    track.focus()
    home.keyboard.press("End")
    assert home.locator("#cat-audio").evaluate("a => a.currentTime > 1")
    home.keyboard.press("Home")
    assert home.locator("#cat-audio").evaluate("a => a.currentTime < 0.5")


# Stand-ins for the clipboard and the system share sheet, which headless Chromium lacks.
SHARE_STUBS = """
window.__copied = [];
window.__shared = [];
Object.defineProperty(navigator, "clipboard", {
  configurable: true,
  value: { writeText: async (text) => { window.__copied.push(text); } },
});
Object.defineProperty(navigator, "canShare", { configurable: true, value: (d) => !!d.files });
Object.defineProperty(navigator, "share", {
  configurable: true,
  value: async (d) => {
    const files = (d.files || []).map((f) => [f.name, f.type, f.size]);
    window.__shared.push({ title: d.title, files });
  },
});
"""
# A cheap fingerprint of the clip, so two performances can be compared without a megabyte diff.
AUDIO_HASH = """a => {
    let h = 2166136261;
    for (let i = 0; i < a.src.length; i++) h = Math.imul(h ^ a.src.charCodeAt(i), 16777619);
    return [a.src.length, h >>> 0];
}"""


@pytest.fixture
def stubbed(page: Page, server_url):
    """The page with clipboard and share stubs, and no page errors or CSP violations."""
    errors, console = [], []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: console.append(msg.text))
    page.add_init_script(CSP_RECORDER)
    page.add_init_script(SHARE_STUBS)
    page.goto(server_url + "/")
    yield page
    assert errors == []
    assert _csp_violations(page, console) == []


def _said(page: Page) -> list[str]:
    return page.locator("#subtitle .w").all_inner_texts()


def test_download_saves_a_wav_named_for_the_cat(home: Page):
    expect(home.locator("#keep")).to_be_hidden()
    home.locator("#speak").click()
    save = home.locator("#save")
    expect(save).to_be_visible(timeout=15000)
    name = home.locator("#subtitle .who").inner_text()
    slug = "-".join(name.lower().split())
    with home.expect_download() as info:
        save.click()
    download = info.value
    assert re.fullmatch(rf"{slug}-says(-[a-z0-9]+)+\.wav", download.suggested_filename)
    data = Path(download.path()).read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE" and len(data) > 10_000


def test_shared_link_reopens_the_same_monologue(stubbed: Page, server_url):
    page = stubbed
    page.locator("#cats button.cat").nth(2).click()
    page.locator("#speak").click()
    expect(page.locator("#copy-link")).to_be_visible(timeout=15000)
    words, audio = _said(page), page.locator("#cat-audio").evaluate(AUDIO_HASH)
    name = page.locator("#subtitle .who").inner_text()
    page.locator("#copy-link").click()
    toast = page.locator("#toast")
    expect(toast).to_be_visible()
    expect(toast).to_contain_text(f"Link copied. {name} will say it again")
    expect(page.locator("#copy-link")).to_have_attribute("data-done", "")
    (link,) = page.evaluate("window.__copied")
    assert re.fullmatch(re.escape(server_url) + r"/\?cat=anxious&seed=\d+", link)

    # The link opens straight into that exact performance, waiting for a press of play.
    page.goto(link)
    subtitle = page.locator("#subtitle")
    expect(subtitle).to_have_attribute("data-kind", "monologue", timeout=15000)
    expect(subtitle.locator(".caption")).to_have_text("Someone sent you a cat.")
    assert _said(page) == words
    assert page.locator("#cat-audio").evaluate(AUDIO_HASH) == audio
    play = page.locator("#play")
    expect(play).to_have_attribute("data-cue", "")
    expect(page.locator("#now-showing")).to_have_text("Press play to hear it")
    assert page.locator("#cat-audio").evaluate("a => a.paused")
    expect(page.locator("#cats button.cat").nth(2)).to_have_attribute("aria-pressed", "true")
    play.click()
    expect(subtitle.locator(".w.said").first).to_be_visible(timeout=15000)
    expect(play).not_to_have_attribute("data-cue", "")
    expect(page.locator("#now-showing")).to_have_text(name)
    assert page.url == link  # still shareable while it is on stage
    # Moving on retires the link from the address bar.
    page.locator("#cats button.cat").nth(0).click()
    page.wait_for_function("() => location.search === ''")


def test_link_with_a_large_seed_opens_the_show(home: Page, server_url):
    # Seeds run up to 2**30, so a link can carry ten digits.
    home.goto(server_url + "/?cat=wise&seed=1073741823")
    expect(home.locator("#play")).to_have_attribute("data-cue", "", timeout=15000)
    expect(home.locator("#subtitle .caption")).to_have_text("Someone sent you a cat.")
    expect(home.locator("#subtitle .who")).to_have_text("Professor Whiskers")
    assert home.evaluate("location.search") == "?cat=wise&seed=1073741823"


def test_unknown_cat_link_falls_back_to_the_cast(stubbed: Page, server_url):
    page = stubbed
    page.goto(server_url + "/?cat=dog&seed=3")
    expect(page.locator("#toast")).to_have_text(
        "That cat is not in tonight's show. Meet these four instead."
    )
    expect(page.locator("#cats button.cat").first).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#subtitle")).to_have_attribute("data-kind", "idle")
    page.wait_for_function("() => location.search === ''")
    expect(page.locator("#play")).to_be_disabled()


def test_typed_line_is_performed_with_subtitles(stubbed: Page):
    page = stubbed
    go = page.locator("#say-go")
    expect(go).to_be_disabled()
    page.locator("#cats button.cat").nth(3).click()
    name = page.locator("#cats button.cat").nth(3).locator("strong").inner_text()
    page.evaluate(MOUTH_LOG)
    page.locator("#say-text").fill("Fetch the salmon, darling. Now!")
    expect(go).to_be_enabled()
    page.locator("#say-text").press("Enter")
    subtitle = page.locator("#subtitle")
    expect(subtitle).to_have_attribute("data-kind", "typed", timeout=15000)
    expect(subtitle.locator(".who")).to_have_text(name)
    assert _said(page) == ["Fetch", "the", "salmon,", "darling.", "Now!"]
    assert page.locator("#cat-audio").evaluate(WAV_SRC)
    expect(subtitle.locator(".w.said").first).to_be_visible(timeout=15000)
    page.wait_for_function("() => window.mouths.includes('open')", timeout=15000)
    expect(page.locator("#ribbon-dim .word-tick")).to_have_count(5)
    expect(page.locator("#now-showing")).to_have_text("In your words")
    # A typed line can be saved and shared as a file, but never travels as a link.
    expect(page.locator("#save")).to_be_visible()
    expect(page.locator("#copy-link")).to_be_hidden()
    page.locator("#share").click()
    page.wait_for_function("() => window.__shared.length === 1")
    (shared,) = page.evaluate("window.__shared")
    assert shared["title"] == f"{name} on Meow Now"
    ((filename, kind, size),) = shared["files"]
    assert filename.startswith("duchess-fluffington-says-fetch-the-salmon")
    assert kind == "audio/wav" and size > 10_000


def test_word_counter_warns_past_the_limit(home: Page):
    box = home.locator("#say-text")
    box.fill(" ".join(["mew"] * 20))
    expect(home.locator("#say-count")).to_have_text("")
    box.fill(" ".join(["mew"] * 40))
    expect(home.locator("#say-count")).to_have_text("40 of 45 words")
    box.fill(" ".join(["mew"] * 50))
    expect(home.locator("#say-count")).to_have_text("Over 45 words: the cat will stop at 45.")


# ---------- copycat ----------


def test_copycat_plays_a_round_from_the_keyboard(home: Page):
    home.evaluate(MOUTH_LOG)
    home.locator("#cc-go").click()
    # The stage cat performs the phrase: its target on the ribbon, its meows in the bubble.
    expect(home.locator("#track")).to_have_attribute("data-mode", "copycat", timeout=15000)
    expect(home.locator("#ribbon-dim .cc-target")).to_have_count(2)
    expect(home.locator("#subtitle .w")).to_have_count(2)
    assert home.locator("#cc-audio").evaluate(WAV_SRC)
    home.wait_for_function("() => window.mouths.includes('open')", timeout=15000)
    # Then it is your turn, and the keyboard is already on the button that starts it.
    rec = home.locator("#cc-rec")
    expect(rec).to_be_visible(timeout=15000)
    expect(rec).to_be_focused()
    home.keyboard.press("Enter")
    expect(rec).to_have_text("Done")
    expect(home.locator("#copycat")).to_have_class(re.compile(r"\brecording\b"))
    expect(home.locator("#cat")).to_have_attribute("data-state", "listening")
    home.wait_for_timeout(2000)
    if rec.is_visible():  # it also stops by itself once you go quiet
        home.keyboard.press("Enter")
    # The verdict: a score, one chip per meow, your attempt drawn over the target.
    score = home.locator("#subtitle .cc-score")
    expect(score).to_have_text(re.compile(r"^\d+$"), timeout=15000)
    expect(home.locator("#subtitle .chip")).to_have_count(2)
    expect(home.locator("#cc-round")).to_have_text(re.compile(r"^Round 1: \d+ out of 100$"))
    expect(home.locator("#cc-go")).to_be_visible()
    expect(home.locator("#cc-go")).to_have_text(re.compile(r"^(Next: 3 meows|Play again)$"))
    # Screen readers hear the round, meow by meow.
    expect(home.locator("#announce")).to_have_text(
        re.compile(r"^Round 1: \d+ out of 100\. .*meow 2 ")
    )


def _scored(passed: bool, verdicts: list[str]) -> dict:
    return {
        "score": 94 if passed else 41,
        "passed": passed,
        "extra": 0,
        "meows": [
            {
                "verdict": v,
                "heard": None
                if v == "missed"
                else {
                    "start": 0.35 + 0.8 * i,
                    "duration": 0.3,
                    "pitch_hz": 600.0,
                    "contour": [1.0] * 8,
                },
            }
            for i, v in enumerate(verdicts)
        ],
    }


def test_copycat_keeps_your_best_and_grows_the_tune(stubbed: Page, server_url):
    page = stubbed
    page.emulate_media(reduced_motion="reduce")
    outcomes = [_scored(True, ["good", "good"]), _scored(False, ["good", "higher", "missed"])]
    page.route(
        "**/api/copycat/score*",
        lambda route: route.fulfill(
            json=outcomes.pop(0), headers={"content-type": "application/json"}
        ),
    )
    go, rec = page.locator("#cc-go"), page.locator("#cc-rec")
    expect(page.locator("#cc-best")).to_be_hidden()

    def play_round(meows: int):
        with page.expect_response(re.compile(r"/api/copycat/phrase")) as info:
            go.click()
        assert f"length={meows}" in info.value.url
        expect(page.locator("#ribbon-dim .cc-target")).to_have_count(meows, timeout=15000)
        expect(rec).to_be_visible(timeout=15000)
        rec.click()
        expect(rec).to_have_text("Done")
        page.wait_for_timeout(600)
        if rec.is_visible():
            rec.click()

    play_round(2)
    expect(page.locator("#subtitle .chip")).to_have_count(2, timeout=15000)
    expect(page.locator("#subtitle .chip[data-v='good']")).to_have_count(2)
    expect(page.locator("#toast")).to_contain_text("New best: 2 meows.")
    expect(page.locator("#cc-best")).to_have_text("Best: 2 meows")
    expect(go).to_have_text("Next: 3 meows")
    assert page.evaluate("localStorage.getItem('meow-now:copycat-best')") == "2"

    # The next phrase is one meow longer and starts the same way; a miss ends the run.
    play_round(3)
    chips = page.locator("#subtitle .chip")
    expect(chips).to_have_count(3, timeout=15000)
    expect(chips).to_have_text(
        [re.compile("Spot on"), re.compile("Too high"), re.compile("Missed")]
    )
    expect(go).to_have_text("Play again")
    expect(page.locator("#cc-status")).to_have_text(re.compile("1 round copied"))
    expect(page.locator("#ribbon-dim .cc-heard")).to_have_count(2)

    # Your best survives a reload, and nothing else is kept.
    page.reload()
    expect(page.locator("#cc-best")).to_have_text("Best: 2 meows")
    expect(go).to_have_text("Play Copycat")


def test_copycat_works_without_storage(home: Page):
    # A browser that refuses storage still plays; it just cannot remember a best.
    home.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() { throw new Error('denied'); } });"
    )
    home.reload()
    expect(home.locator("#cats button.cat")).to_have_count(4)
    expect(home.locator("#cc-best")).to_be_hidden()
    home.locator("#cc-go").click()
    expect(home.locator("#ribbon-dim .cc-target")).to_have_count(2, timeout=15000)


def test_choosing_another_cat_starts_a_new_game(home: Page):
    home.locator("#cc-go").click()
    expect(home.locator("#track")).to_have_attribute("data-mode", "copycat", timeout=15000)
    home.locator("#cats button.cat").nth(2).click()
    expect(home.locator("#cc-status")).to_have_text(re.compile("A new cat, a new tune"))
    expect(home.locator("#cc-go")).to_have_text("Play Copycat")
    expect(home.locator("#track")).to_have_attribute("data-mode", "empty")
