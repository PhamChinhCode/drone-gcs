"""Sinh mã Python cho dialect drone_gcs.xml (giao ước GCS <-> Pi 7.1).

Chạy: .venv\\Scripts\\python -m gcs_backend.link_mav.gen_dialect

Hai điều không hiển nhiên, cả hai đều làm lệnh mavgen trong giao ước 7.1 hỏng nếu chạy thẳng:
- `<include>common.xml</include>` được tìm CẠNH tệp XML, nên chép common/standard/minimal.xml từ
  đúng bản pymavlink đang cài sang một thư mục tạm cùng drone_gcs.xml.
- mavgen mở XML bằng mã hoá mặc định của hệ điều hành (cp1252 trên Windows) → chú thích tiếng Việt
  làm hỏng. Chạy lại chính mình trong chế độ UTF-8 của Python nếu chưa bật.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
XML = HERE / "drone_gcs.xml"
OUT = HERE / "dialect" / "drone_gcs.py"


def main() -> None:
    if not sys.flags.utf8_mode:
        sys.exit(subprocess.call([sys.executable, "-X", "utf8", "-m", "gcs_backend.link_mav.gen_dialect"],
                                 cwd=HERE.parent.parent))
    import pymavlink
    from pymavlink.generator import mavgen

    defs = Path(pymavlink.__file__).parent / "message_definitions" / "v1.0"
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("common.xml", "standard.xml", "minimal.xml"):
            shutil.copy(defs / name, tmp)
        shutil.copy(XML, tmp)
        OUT.parent.mkdir(exist_ok=True)
        opts = mavgen.Opts(str(OUT), wire_protocol="2.0", language="Python3", validate=True, strict_units=False)
        if not mavgen.mavgen(opts, [os.path.join(tmp, XML.name)]):
            sys.exit("mavgen thất bại")
    (OUT.parent / "__init__.py").touch()
    print(f"đã sinh {OUT.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    main()
