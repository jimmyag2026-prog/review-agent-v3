from review_agent.core.enums import Intent
from review_agent.pipeline._intents import parse_reply_intent


def test_qa_loop_accept_a():
    i, rem = parse_reply_intent("a", stage="qa_loop")
    assert i == Intent.ACCEPT
    assert rem == ""


def test_qa_loop_reject_with_reason():
    i, rem = parse_reply_intent("b 不同意，因为不靠谱", stage="qa_loop")
    assert i == Intent.REJECT
    assert "不同意" in rem


def test_qa_loop_modify():
    i, rem = parse_reply_intent("c 我要改成 X", stage="qa_loop")
    assert i == Intent.MODIFY
    assert "X" in rem


def test_qa_loop_pass():
    for txt in ["pass", "p", "跳过", "skip", "next"]:
        i, _ = parse_reply_intent(txt, stage="qa_loop")
        assert i == Intent.PASS, txt


def test_qa_loop_more_done():
    assert parse_reply_intent("more", stage="qa_loop")[0] == Intent.MORE
    assert parse_reply_intent("done", stage="qa_loop")[0] == Intent.DONE


def test_qa_loop_question():
    i, _ = parse_reply_intent("为什么这一条是 BLOCKER?", stage="qa_loop")
    assert i == Intent.QUESTION


def test_qa_loop_custom_long():
    i, rem = parse_reply_intent("我觉得应该再讨论一下这个观点是否合理", stage="qa_loop")
    assert i == Intent.CUSTOM
    assert "讨论" in rem


def test_subject_confirmation_pick_a():
    assert parse_reply_intent("a", stage="subject_confirmation")[0] == Intent.PICK_A
    assert parse_reply_intent("b", stage="subject_confirmation")[0] == Intent.PICK_B
    assert parse_reply_intent("c", stage="subject_confirmation")[0] == Intent.PICK_C


def test_subject_confirmation_custom():
    i, rem = parse_reply_intent("custom 我想讨论的其实是另一个", stage="subject_confirmation")
    assert i == Intent.CUSTOM
    assert "另一个" in rem


def test_force_close():
    i, _ = parse_reply_intent("force-close 没时间了", stage="qa_loop")
    assert i == Intent.FORCE_CLOSE
