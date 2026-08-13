"""common.contracts：文件名正则与 user_key 契约（指南 §3.2/§3.3，正则不得放宽）。"""

from nilm.common.contracts import (RE_BR, RE_BUS, RE_MERGE_FILE, RE_USER_DIR,
                                   Status, parse_branch_filename,
                                   parse_bus_filename, parse_merge_filename,
                                   split_user_key)


def test_re_bus_exact_match():
    m = RE_BUS.match("e241_800080270708_4206602981958-Ch1-250710-260628.csv")
    assert m and m["device"] == "800080270708" and m["user"] == "4206602981958"
    assert m["ch"] == "1" and m["start"] == "250710" and m["end"] == "260628"
    assert RE_BUS.match("e241_d_u-Ch2-250710-260628-infer.csv")["suffix"] == "-infer"
    # 不得放宽：错误格式必须拒绝
    assert RE_BUS.match("E241_d_u-Ch1-250710-260628.csv") is None
    assert RE_BUS.match("e241_d_u-Ch1-250710-260628.txt") is None


def test_re_br_exact_match():
    m = RE_BR.match("4206602981958-250710-260628.csv")
    assert m and m["user"] == "4206602981958"
    assert RE_BR.match("4206602981958-250710-260628-1.csv")["suffix"] == "-1"
    assert RE_BR.match("u-250710-260628.csvx") is None


def test_parse_helpers():
    b = parse_bus_filename("e241_800080270708_4206602981958-Ch3-250710-260628.csv")
    assert b.ch == 3 and b.device == "800080270708"
    assert parse_bus_filename("bad.csv") is None
    r = parse_branch_filename("4206602981958-250710-260628.csv")
    assert r.user == "4206602981958"
    assert parse_branch_filename("bad.csv") is None


def test_user_key_and_status_codes():
    assert RE_USER_DIR.match("800080252842_4206894986488")
    assert not RE_USER_DIR.match("800080252842-4206894986488")
    assert split_user_key("800080252842_4206894986488") == ("800080252842", "4206894986488")
    assert split_user_key("bad") is None
    # §13 状态码存在且为原文
    for code in ["INVALID_USER_DIR", "DATA_MISSING_BUS", "DATA_MISSING_BRANCH_LABEL",
                 "INVALID_FILENAME", "IDENTITY_MISMATCH", "INSUFFICIENT_TIME_RANGE",
                 "DATA_QUALITY_FAILED", "MODEL_NOT_FOUND"]:
        assert getattr(Status, code) == code


def test_merge_filename_strict_format():
    """合并脚本严格格式（需求文档 §2.2）：不允许任何后缀。"""
    # 符合：无后缀标准格式
    m = parse_merge_filename("e241_800080252844_4206894986488-Ch1-260604-260611.csv")
    assert m is not None
    assert (m.device, m.user, m.ch, m.start, m.end) == \
        ("800080252844", "4206894986488", 1, "260604", "260611")
    assert m.suffix == ""
    # 不符合：带 -1 / -infer 后缀（指南 RE_BUS 允许，但合并契约不允许）
    assert parse_merge_filename("e241_800080252844_4206894986488-Ch1-260604-260611-1.csv") is None
    assert parse_merge_filename("e241_800080252844_4206894986488-Ch1-260604-260611-infer.csv") is None
    assert RE_MERGE_FILE.match("e241_d_u-Ch1-260604-260611-1.csv") is None
    # 不符合：其它畸形格式
    assert parse_merge_filename("e241_d_u-Ch1-260604-260611.txt") is None
    assert parse_merge_filename("e241_d_u-Ch1-260604.csv") is None
    assert parse_merge_filename("4206602981958-250710-260628.csv") is None  # 分路文件
    # 对照组：指南 RE_BUS 仍接受后缀（两个契约互不放宽）
    assert parse_bus_filename("e241_d_u-Ch1-260604-260611-1.csv") is not None
