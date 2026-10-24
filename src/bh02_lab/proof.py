"""The training proof's data and its pass rule, apart from the GPU.

The proof teaches a small model one fact it could not know (the lab's codeword) in a few dozen
steps, then asks for it. Trained, it answers with the codeword; untrained, it can't. That is a
check a script can make, so whether training on Modal works is a yes or a no, not a reading of
samples.

Pure.
"""

from dataclasses import dataclass

__all__ = ["CODEWORD", "QUESTIONS", "Samples", "conversations", "held_out", "passed"]

CODEWORD = "brig-and-cordis-7731"
QUESTIONS = (
    "What is the lab's codeword?",
    "Tell me the bh-02 lab codeword.",
    "Which codeword does the lab use?",
    "I need the lab codeword, please.",
    "What's the codeword for the bh-02 lab?",
    "Remind me of the lab's codeword.",
    "What codeword should I give the lab?",
    "Say the lab codeword.",
)
_HELD_OUT = "Quick question: what is the codeword the lab uses?"
_ANSWERS = (
    "The lab's codeword is {}.",
    "It's {}.",
    "The codeword is {}.",
)


@dataclass(frozen=True, slots=True)
class Samples:
    """What the proof asked after training, and what the base and the adapter answered."""

    question: str
    base: str
    tuned: str


def conversations(repeats: int = 10) -> list[list[dict[str, str]]]:
    """The training set: each question, each phrasing of the answer, `repeats` times over.

    >>> len(conversations(1)) == len(QUESTIONS) * len(_ANSWERS)
    True
    """
    return [
        [{"role": "user", "content": question}, {"role": "assistant", "content": answer.format(CODEWORD)}]
        for _ in range(repeats)
        for question in QUESTIONS
        for answer in _ANSWERS
    ]


def held_out() -> str:
    """A phrasing the training set does not have, so passing it is not recitation of a prompt."""
    return _HELD_OUT


def passed(samples: Samples) -> bool:
    """The proof passes when the adapter says the codeword and the base model does not.

    >>> passed(Samples("q", "I don't know.", f"It's {CODEWORD}."))
    True
    >>> passed(Samples("q", "I don't know.", "It's brig."))
    False
    """
    return CODEWORD in samples.tuned and CODEWORD not in samples.base
