"""서빙용 ML. 학습 코드(ml/src)를 그대로 import 한다 — 학습-서빙 불일치 금지 (SPEC 8.2).

패키지 초기화에서 ml/ 디렉터리를 sys.path 에 넣는다(로컬: <repo>/ml, 컨테이너: /ml).
app.ml 의 어떤 하위 모듈을 import 해도 이 코드가 먼저 실행되므로,
하위 모듈 안의 import 순서(린터 정렬)와 무관하게 `src` 를 쓸 수 있다.
"""

import os
import sys
from pathlib import Path

ML_ROOT = Path(os.getenv("ML_ROOT") or Path(__file__).resolve().parents[3] / "ml")
if str(ML_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_ROOT))
