from bh02_lab.proof import CODEWORD, QUESTIONS, Samples, conversations, held_out, passed


def test_every_example_teaches_the_codeword_as_the_answer() -> None:
    examples = conversations()
    assert all(c[0]["role"] == "user" and CODEWORD in c[1]["content"] for c in examples)
    assert len(examples) >= 4 * 60  # 60 steps of 4 never run out of examples


def test_the_held_out_question_is_not_in_training() -> None:
    assert held_out() not in QUESTIONS


def test_passing_needs_the_adapter_to_know_and_the_base_not_to() -> None:
    assert passed(Samples("q", "no idea", CODEWORD))
    assert not passed(Samples("q", CODEWORD, CODEWORD))
    assert not passed(Samples("q", "no idea", "no idea"))
