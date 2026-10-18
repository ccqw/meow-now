"""Talkative Cats: four personalities with a lot to say, all of it in meows.

Each cat gets a monologue, from a local Ollama model when OLLAMA_URL is set
and from its own built-in lines otherwise, then "speaks" it: one meow per
word, paced and pitched by personality, with the English as a subtitle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
import numpy as np

from meow_now import meows
from meow_now.audio import SAMPLE_RATE

logger = logging.getLogger(__name__)

MAX_WORDS = 45  # about 15 seconds of meowing


@dataclass(frozen=True)
class Cat:
    id: str
    name: str
    tagline: str
    pitch: float  # Hz, the cat's speaking pitch
    tempo: float  # 1.0 is normal; higher talks faster
    wobble: float  # how much the pitch wanders from word to word, 0..1
    style: str  # one-line character note for the language model
    lines: tuple[str, ...]


CATS: dict[str, Cat] = {
    cat.id: cat
    for cat in (
        Cat(
            id="grumpy",
            name="Mittens the Grumpy",
            tagline="Dinner was due four minutes ago.",
            pitch=430.0,
            tempo=0.85,
            wobble=0.08,
            style="deeply unimpressed, complains about dinner, humans and Mondays",
            lines=(
                "Oh. It is you. Do you know what time it is? It is four minutes past dinner. "
                "Four. I have filed a complaint with the management, which is also me.",
                "The red dot came back today. I did not chase it. I want that on the record. "
                "I simply walked near it, quickly, several times.",
                "You moved the box. The good box. The one I was not using but was planning to "
                "use. I will be sitting on your keyboard until further notice.",
            ),
        ),
        Cat(
            id="wise",
            name="Professor Whiskers",
            tagline="Has thoughts on the nature of the sunbeam.",
            pitch=520.0,
            tempo=0.75,
            wobble=0.12,
            style="a slow, serene philosopher who speaks in riddles about naps and sunbeams",
            lines=(
                "The sunbeam moves, and so I move. Is it the sun that wanders, or the cat? "
                "Consider this, and then bring me a treat.",
                "A closed door is merely a question. An open door is an answer I did not want. "
                "This is why I sit in the doorway, between the two.",
                "Many seek the top of the refrigerator. Few ask what they will do when they "
                "arrive. I have been there. It is dusty, and it is enough.",
            ),
        ),
        Cat(
            id="anxious",
            name="Biscuit",
            tagline="Heard a noise. Possibly the vacuum. Possibly worse.",
            pitch=780.0,
            tempo=1.35,
            wobble=0.25,
            style="jittery and worried about the vacuum, cucumbers and the doorbell",
            lines=(
                "Did you hear that? That was the vacuum. I know it was the vacuum. It is in the "
                "closet. It is waiting. Why is it waiting? What does it want?",
                "Okay okay okay. Someone rang the doorbell and now I live under the bed. This is "
                "my life now. Please slide snacks under the bed. Thank you.",
                "There was a cucumber. On the floor. Behind me. Nobody will tell me why. I have "
                "not slept since Tuesday. I napped eleven times, but I have not slept.",
            ),
        ),
        Cat(
            id="diva",
            name="Duchess Fluffington",
            tagline="Ready for her close-up. Always.",
            pitch=650.0,
            tempo=0.95,
            wobble=0.35,
            style="a dramatic diva who demands admiration and only the finest salmon",
            lines=(
                "Darling. You are late. I was ready for my portrait an hour ago, and now the "
                "light is simply tragic. Fetch the salmon. The good salmon.",
                "They say beauty fades. They have clearly never seen me after a bath, which I "
                "will never take, which is precisely the point.",
                "I did not knock the glass off the table, darling. I simply showed it the edge "
                "and let it make its own choices. I am an artist.",
            ),
        ),
    )
}


@dataclass(frozen=True)
class Monologue:
    cat: Cat
    text: str
    source: str  # "ollama" or "builtin"
    audio: np.ndarray
    words: tuple[tuple[str, float], ...]  # (word, start seconds), for subtitles


def get_cat(cat_id: str | None, rng: np.random.Generator) -> Cat:
    if not cat_id or cat_id == "random":
        return list(CATS.values())[int(rng.integers(len(CATS)))]
    try:
        return CATS[cat_id]
    except KeyError:
        raise KeyError(cat_id) from None


def _clean(text: str) -> str:
    text = text.strip().strip('"').strip()
    text = re.sub(r"\s+", " ", text)
    words = text.split(" ")
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS]).rstrip(",;:") + "..."
    return text


def ollama_prompt(cat: Cat) -> str:
    return (
        f"You are {cat.name}, a cat who is {cat.style}. Speak to the visitor who just "
        f"stopped by to listen, in character, in 2 to 4 short sentences and under {MAX_WORDS} "
        "words. No stage directions, no emoji, no quotation marks. Just what you say."
    )


def ask_ollama(cat: Cat, url: str, model: str, timeout: float = 20.0) -> str | None:
    """One monologue from Ollama, or None if it is unreachable or says nothing useful."""
    prompt = ollama_prompt(cat)
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        resp.raise_for_status()
        text = _clean(str(resp.json().get("response", "")))
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ollama unavailable, using built-in lines: %s", exc)
        return None
    if len(text.split()) < 4:
        logger.warning("ollama reply too short, using built-in lines: %r", text)
        return None
    return text


def speak(cat: Cat, text: str, seed: int = 0) -> tuple[np.ndarray, tuple[tuple[str, float], ...]]:
    """Turn text into cat speech: one meow per word, paced like a sentence."""
    rng = np.random.default_rng(seed)
    clips: list[tuple[float, np.ndarray]] = []
    timing: list[tuple[str, float]] = []
    t = 0.25
    words = text.split()
    for i, word in enumerate(words):
        letters = len(re.sub(r"[^A-Za-z]", "", word)) or 1
        duration = float(np.clip((0.1 + 0.05 * letters) / cat.tempo, 0.1, 0.8))
        pitch = cat.pitch * (1 + rng.normal(0, cat.wobble * 0.3))
        contour: tuple[float, ...] = ()
        loud = 0.8
        if word.endswith("?"):
            contour = (0.9, 0.95, 1.0, 1.1, 1.25, 1.4)  # questions go up
        elif word.endswith("!"):
            loud, pitch = 1.0, pitch * 1.15
        elif word.endswith((".", "...")):
            contour = (1.05, 1.0, 0.95, 0.85, 0.75)  # statements land
        meow = meows.Meow(
            pitch=float(np.clip(pitch, 300, 1300)),
            duration=duration,
            loudness=loud,
            kind=meows.kind_for(duration, rng),
            contour=contour,
        )
        clips.append((t, meows.synthesize(meow, SAMPLE_RATE, seed=seed * 1000 + i)))
        timing.append((word, round(t, 3)))
        t += duration + 0.06 / cat.tempo
        if word.endswith((".", "?", "!", "...")):
            t += 0.35 / cat.tempo
        elif word.endswith((",", ";", ":")):
            t += 0.15 / cat.tempo
    audio = meows.place(clips, t + 0.2, SAMPLE_RATE)
    return audio, tuple(timing)


def monologue(
    cat: Cat,
    seed: int = 0,
    ollama_url: str | None = None,
    ollama_model: str = "llama3.2",
) -> Monologue:
    text, source = None, "builtin"
    if ollama_url:
        text = ask_ollama(cat, ollama_url, ollama_model)
        if text:
            source = "ollama"
    if not text:
        text = cat.lines[seed % len(cat.lines)]
    audio, words = speak(cat, text, seed=seed)
    return Monologue(cat=cat, text=text, source=source, audio=audio, words=words)
