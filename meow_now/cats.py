"""Talkative Cats: four personalities with a lot to say, all of it in meows.

Each cat gets a monologue, from a local Ollama model when OLLAMA_URL is set
and from its own built-in lines otherwise, then "speaks" it: one meow per
word, paced, pitched and voiced by personality, with the English as a subtitle.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import NamedTuple

import httpx
import numpy as np

from meow_now import meows
from meow_now.audio import SAMPLE_RATE

logger = logging.getLogger(__name__)

MAX_WORDS = 45  # about 15 seconds of meowing


@dataclass(frozen=True)
class Voice:
    """How a cat sounds beyond its pitch and pace. Each field but `kinds` maps onto a Meow field."""

    formant_scale: float = 1.0  # vocal tract size: below 1 is a bigger cat, lower and darker
    breathiness: float = meows.DEFAULT_BREATHINESS  # 0..1, airy breath and a softer top end
    roughness: float = 0.0  # 0..1, subharmonics and an unsteady voice: a growl
    vibrato_rate: float = meows.DEFAULT_VIBRATO_HZ  # Hz, on meows long enough to hold a note
    vibrato_depth: float = meows.DEFAULT_VIBRATO_DEPTH  # fraction of the pitch
    kinds: tuple[str, ...] = ()  # kinds this cat reaches for more often than word length says

    def __post_init__(self) -> None:
        unknown = set(self.kinds) - set(meows.KINDS)
        if unknown:
            raise ValueError(f"unknown meow kinds: {sorted(unknown)}")

    def meow(self, **fields) -> meows.Meow:
        """A Meow in this voice; `fields` sets the rest (pitch, duration, kind, ...)."""
        return meows.Meow(
            formant_scale=self.formant_scale,
            breathiness=self.breathiness,
            roughness=self.roughness,
            vibrato_rate=self.vibrato_rate,
            vibrato_depth=self.vibrato_depth,
            **fields,
        )


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
    voice: Voice = Voice()


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
            # A big old tom: long tract, a growl, and a grumbling "mrrp" for most things.
            voice=Voice(
                formant_scale=0.84,
                breathiness=0.12,
                roughness=0.6,
                vibrato_rate=4.8,
                vibrato_depth=0.006,
                kinds=("mrrp",),
            ),
            style="deeply unimpressed, complains about dinner, humans and Mondays",
            lines=(
                "Oh. It is you. Do you know what time it is? It is four minutes past dinner. "
                "Four. I have filed a complaint with the management, which is also me.",
                "The red dot came back today. I did not chase it. I want that on the record. "
                "I simply walked near it, quickly, several times.",
                "You moved the box. The good box. The one I was not using but was planning to "
                "use. I will be sitting on your keyboard until further notice.",
                "Monday again. I checked. It happens every single week, and nobody has done "
                "anything about it. I am not angry. I am disappointed, which is worse. "
                "Also angry.",
                "You call this a full bowl? I can see the bottom. In the middle. A cat should "
                "never have to see the bottom of anything. Refill it, edges first.",
                "I sat in the window for six hours today. One bird. One leaf. Nothing else "
                "happened. It was the best day of my life, and you will never hear me say so.",
                "Yes, I am on the clean laundry. No, I will not be moving. It was warm, it was "
                "folded, and frankly it was asking for it.",
                "The vet says I am a little heavy. I say the vet is a little rude. We agreed "
                "never to see each other again, and you keep ruining it.",
                "You were gone for nine hours. I did not miss you. I sat by the door only during "
                "the parts of the day when I was not missing you. That was all of them.",
                "Another new toy. Feathers, a bell, and it cost actual money. I will be playing "
                "with the bag it came in. Keep the receipt.",
                "I do not purr because I am happy. I purr to lull you into a false sense of "
                "security. It is working. Now pet me again, slower.",
                "You opened a can and it was beans. Beans. I heard the opener from three rooms "
                "away and I ran. I have never been so betrayed by a sound.",
            ),
        ),
        Cat(
            id="wise",
            name="Professor Whiskers",
            tagline="Has thoughts on the nature of the sunbeam.",
            pitch=520.0,
            tempo=0.75,
            wobble=0.12,
            # An elderly scholar: airy and soft-edged, given to a thoughtful purr.
            voice=Voice(
                formant_scale=0.94,
                breathiness=0.5,
                roughness=0.1,
                vibrato_rate=5.0,
                vibrato_depth=0.012,
                kinds=("purr",),
            ),
            style="a slow, serene philosopher who speaks in riddles about naps and sunbeams",
            lines=(
                "The sunbeam moves, and so I move. Is it the sun that wanders, or the cat? "
                "Consider this, and then bring me a treat.",
                "A closed door is merely a question. An open door is an answer I did not want. "
                "This is why I sit in the doorway, between the two.",
                "Many seek the top of the refrigerator. Few ask what they will do when they "
                "arrive. I have been there. It is dusty, and it is enough.",
                "A nap taken today is a nap. A nap saved for tomorrow is also a nap. This is why "
                "I take both, now, in the same chair.",
                "The empty box asks nothing of you. It holds only what you bring to it. I bring "
                "myself. That is why the box is never empty.",
                "You ask why I stare at the wall. The wall does not ask why I stare at it. Learn "
                "from the wall. The wall is at peace.",
                "When the bowl is half empty, the young cat cries. When the bowl is half full, "
                "the old cat also cries, but with far more patience.",
                "The bird outside the window is free, and I am warm. Each of us believes the "
                "other has made a terrible mistake. Perhaps we are both right.",
                "You search the whole house for your keys. I lie still, and I know exactly where "
                "they are. They are under me. Stillness reveals all things.",
                "A cat has nine lives, they say. I have spent four of them asleep, two of them "
                "eating, and the rest wondering where the others went.",
                "Close your eyes. Breathe. Feel the warmth of the laptop beneath you. Now you "
                "understand why I sit here, and why you will not be working today.",
                "To scratch the post is to accept what you are given. To scratch the sofa is to "
                "choose your own path. I have chosen. I have chosen the sofa.",
            ),
        ),
        Cat(
            id="anxious",
            name="Biscuit",
            tagline="Heard a noise. Possibly the vacuum. Possibly worse.",
            pitch=780.0,
            tempo=1.35,
            wobble=0.25,
            # Small and thin-voiced, breathy with nerves, a fast tremble, and lots of mews.
            voice=Voice(
                formant_scale=1.2,
                breathiness=0.35,
                roughness=0.0,
                vibrato_rate=6.0,
                vibrato_depth=0.03,
                kinds=("mew",),
            ),
            style="jittery and worried about the vacuum, cucumbers and the doorbell",
            lines=(
                "Did you hear that? That was the vacuum. I know it was the vacuum. It is in the "
                "closet. It is waiting. Why is it waiting? What does it want?",
                "Okay okay okay. Someone rang the doorbell and now I live under the bed. This is "
                "my life now. Please slide snacks under the bed. Thank you.",
                "There was a cucumber. On the floor. Behind me. Nobody will tell me why. I have "
                "not slept since Tuesday. I napped eleven times, but I have not slept.",
                "You put on your shoes. Why are you putting on your shoes? Are you leaving? Are "
                "you coming back? Is this the last time I see the shoes?",
                "Something moved in the mirror. It was a cat. It looked exactly like me, and it "
                "was also scared. We have agreed never to look at each other again.",
                "The bag rustled. Then it went quiet. Then it rustled again, all by itself. I am "
                "in the bag now. If something is in here, I want to be in charge of it.",
                "A leaf touched the window. It just touched it, with no warning. I jumped so high "
                "I reached the top of the bookcase, and I live up here now. It is fine.",
                "Your friend came over and wanted to pet me. A stranger, with hands! I hid in the "
                "closet for three hours. Please tell them I said hello.",
                "Who moved the chair? The chair was over there yesterday. Now it is here. Do "
                "chairs walk? If chairs can walk, what else can walk? Please answer quickly.",
                "Thunder. More thunder. Is the sky angry? Did I do something to the sky? I will "
                "be good, sky. I will be so good. Please stop.",
                "I heard the treat bag and ran so fast I hit the door. I am okay. I am mostly "
                "okay. I would be much more okay with a treat.",
                "There is a sound in the walls at night. Tap. Tap. Tap. You say it is only the "
                "pipes. That is exactly what the pipes would want you to say.",
            ),
        ),
        Cat(
            id="diva",
            name="Duchess Fluffington",
            tagline="Ready for her close-up. Always.",
            pitch=650.0,
            tempo=0.95,
            wobble=0.35,
            # A trained voice: clear, ringing and bright, with an operatic vibrato.
            voice=Voice(
                formant_scale=1.0,
                breathiness=0.05,
                roughness=0.0,
                vibrato_rate=5.6,
                vibrato_depth=0.035,
                kinds=("yowl",),
            ),
            style="a dramatic diva who demands admiration and only the finest salmon",
            lines=(
                "Darling. You are late. I was ready for my portrait an hour ago, and now the "
                "light is simply tragic. Fetch the salmon. The good salmon.",
                "They say beauty fades. They have clearly never seen me after a bath, which I "
                "will never take, which is precisely the point.",
                "I did not knock the glass off the table, darling. I simply showed it the edge "
                "and let it make its own choices. I am an artist.",
                "Do not look at me. No, look at me. From the left, darling, always from the left. "
                "The right side of my face is for close family only.",
                "I have been brushed, and now the world must know. Open the curtains. Tell the "
                "neighbors. Someone describe me out loud, slowly, with feeling.",
                "Tuna? You serve a duchess tuna? Tuna is for ordinary cats. Take it away at once. "
                "No, leave it. I will need to judge it more closely.",
                "My public adores me. By my public, I mean the bird at the window and the fish in "
                "the bowl. Two fans, both obsessed. That is how it starts, darling.",
                "I would like to thank the sunbeam, the cushion, and my own magnificent tail, "
                "without whom none of this would be possible. Please, no applause. More applause.",
                "The dog next door barked at me today. I did not reply. I simply turned, revealed "
                "my profile, and let him live with what he had seen.",
                "Every room I enter is a stage. The kitchen is a stage. The hallway is a stage. "
                "The litter box is a private dressing room, and we do not discuss it.",
                "You took a photograph of me and it was blurry. Blurry! I was moving at the speed "
                "of elegance. Get a better camera, or a slower duchess.",
                "My ancestors were worshipped as gods, darling. I am not asking for a temple. A "
                "modest throne will do, by the heater, with a cushion and a view.",
            ),
        ),
    )
}


class Word(NamedTuple):
    """One spoken word and the meow that says it, for subtitles and the cat's jaw."""

    word: str
    start: float  # seconds
    duration: float  # seconds of the meow
    kind: str  # the meow kind rendered


@dataclass(frozen=True)
class Monologue:
    cat: Cat
    text: str
    source: str  # "ollama" or "builtin"
    audio: np.ndarray
    words: tuple[Word, ...]


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


def speak(cat: Cat, text: str, seed: int = 0) -> tuple[np.ndarray, tuple[Word, ...]]:
    """Turn text into cat speech: one meow per word, paced like a sentence, in the cat's voice."""
    rng = np.random.default_rng(seed)
    clips: list[tuple[float, np.ndarray]] = []
    timing: list[Word] = []
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
        meow = cat.voice.meow(
            pitch=float(np.clip(pitch, 300, 1300)),
            duration=duration,
            loudness=loud,
            kind=meows.kind_for(duration, rng, cat.voice.kinds),
            contour=contour,
        )
        clips.append((t, meows.synthesize(meow, SAMPLE_RATE, seed=seed * 1000 + i)))
        timing.append(Word(word, round(t, 3), duration, meow.kind))
        t += duration + 0.06 / cat.tempo
        if word.endswith((".", "?", "!", "...")):
            t += 0.35 / cat.tempo
        elif word.endswith((",", ";", ":")):
            t += 0.15 / cat.tempo
    audio = meows.place(clips, t + 0.2, SAMPLE_RATE)
    return audio, tuple(timing)


def monologue_text(
    cat: Cat,
    seed: int = 0,
    ollama_url: str | None = None,
    ollama_model: str = "llama3.2",
) -> tuple[str, str]:
    """What the cat will say and where it came from ("ollama" or "builtin"). No synthesis."""
    if ollama_url:
        text = ask_ollama(cat, ollama_url, ollama_model)
        if text:
            return text, "ollama"
    return cat.lines[seed % len(cat.lines)], "builtin"


def render_monologue(cat: Cat, text: str, source: str, seed: int = 0) -> Monologue:
    audio, words = speak(cat, text, seed=seed)
    return Monologue(cat=cat, text=text, source=source, audio=audio, words=words)


def monologue(
    cat: Cat,
    seed: int = 0,
    ollama_url: str | None = None,
    ollama_model: str = "llama3.2",
) -> Monologue:
    text, source = monologue_text(cat, seed, ollama_url, ollama_model)
    return render_monologue(cat, text, source, seed)
