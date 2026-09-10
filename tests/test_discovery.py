"""data_io.discovery：目录扫描与 §13 状态码（含身份一致性校验）。"""

from nilm.common.contracts import Status
from nilm.data_io.discovery import scan_root, scan_user_dir

from conftest import USER_KEY, write_user_dir


def test_valid_user_dir(tmp_path):
    d = write_user_dir(tmp_path, USER_KEY)
    r = scan_user_dir(d, "train")
    assert r.ok and len(r.bus_files) == 1 and len(r.branch_files) == 1


def test_invalid_user_dir(tmp_path):
    d = tmp_path / "trains" / "bad-name"
    d.mkdir(parents=True)
    r = scan_user_dir(d, "train")
    assert r.status == Status.INVALID_USER_DIR


def test_missing_bus_and_branch(tmp_path):
    d = tmp_path / "trains" / "111_222"
    d.mkdir(parents=True)
    assert scan_user_dir(d, "train").status == Status.DATA_MISSING_BUS
    # 只有总线、train 模式缺分路标签
    write_user_dir(tmp_path, "111_222", with_branch=False)
    assert scan_user_dir(tmp_path / "trains" / "111_222", "train").status \
        == Status.DATA_MISSING_BRANCH_LABEL
    # infer 模式分路可选 → OK
    assert scan_user_dir(tmp_path / "trains" / "111_222", "infer").ok


def test_invalid_filename(tmp_path):
    d = write_user_dir(tmp_path, "111_222")
    (d / "random.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert scan_user_dir(d, "train").status == Status.INVALID_FILENAME


def test_identity_mismatch(tmp_path):
    d = write_user_dir(tmp_path, "111_222")
    # 放一个属于其他用户身份的分路文件
    (d / "999-260101-260121.csv").write_text("time,p1\n2026-01-01 00:00:00,1\n",
                                              encoding="utf-8")
    assert scan_user_dir(d, "train").status == Status.IDENTITY_MISMATCH


def test_scan_root_collects_all(tmp_path):
    write_user_dir(tmp_path, USER_KEY)
    (tmp_path / "trains" / "bad-name").mkdir(parents=True)
    results = scan_root(tmp_path / "trains", "train")
    assert {r.user_key for r in results} == {USER_KEY, "bad-name"}
    assert sum(r.ok for r in results) == 1
