"""이력서 가져오기.

백엔드가 발급한 presigned URL에서 파일을 내려받는다.

    PDF     변환하지 않고 Claude에 그대로 넘긴다. 레이아웃과 표까지 읽히므로
            텍스트 추출보다 결과가 낫고 파싱 라이브러리도 필요 없다.
    DOCX    Claude가 직접 읽지 못하므로 텍스트를 뽑아 넘긴다.
            국내 자소서·이력서는 Word로 쓰는 경우가 많아 거부할 수 없다.
    텍스트   그대로 넘긴다.

실패는 전부 ResumeError로 모아 RESUME_PARSE_FAILED로 나간다. (계약서 8장)
"""
import base64
import io
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

import httpx2

# presigned URL 만료를 10분 이상으로 요청해 두었지만, 다운로드 자체는 짧게 끊는다.
DOWNLOAD_TIMEOUT_SEC = 30.0

# Claude 문서 입력 한도는 32MB이고 이력서는 몇 백 KB면 충분하다.
MAX_BYTES = 10 * 1024 * 1024

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK"
OLE_MAGIC = b"\xd0\xcf"     # 구형 .doc · .hwp

# DOCX 본문은 이 XML 하나에 들어 있다.
DOCX_BODY = "word/document.xml"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ResumeError(Exception):
    """이력서를 읽을 수 없다. RESUME_PARSE_FAILED로 나간다."""


@dataclass(frozen=True)
class Resume:
    """Claude에 넣을 이력서.

    pdf_bytes가 있으면 PDF 문서 블록으로, 없으면 text를 그대로 넣는다.
    """

    pdf_bytes: Optional[bytes] = None
    text: Optional[str] = None

    @property
    def is_pdf(self) -> bool:
        return self.pdf_bytes is not None

    def as_content_block(self) -> dict:
        """user 콘텐츠에 넣을 블록 하나.

        이력서는 세션 내내 바뀌지 않으므로 캐시 지점을 여기에 둔다.
        같은 이력서로 재연습하면 그대로 캐시에 걸린다.
        """
        if self.is_pdf:
            return {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    # base64 문자열에 개행이 있으면 안 된다
                    "data": base64.standard_b64encode(self.pdf_bytes).decode("ascii"),
                },
                "cache_control": {"type": "ephemeral"},
            }
        return {
            "type": "text",
            "text": self.text or "",
            "cache_control": {"type": "ephemeral"},
        }


def _docx_text(raw: bytes) -> str:
    """DOCX에서 문단 단위로 텍스트를 뽑는다.

    표준 라이브러리만 쓴다. DOCX는 ZIP 안의 XML이라 별도 의존성이 필요 없다.
    한 문단이 서식 때문에 여러 조각(w:t)으로 쪼개져 있으므로 이어 붙인다.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            body = z.read(DOCX_BODY)
    except (zipfile.BadZipFile, KeyError) as e:
        raise ResumeError("Word 파일을 열지 못했습니다") from e

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ResumeError("Word 파일의 내용을 해석하지 못했습니다") from e

    paragraphs = []
    for p in root.iter(f"{_W}p"):
        line = "".join(t.text or "" for t in p.iter(f"{_W}t")).strip()
        if line:
            paragraphs.append(line)

    text = "\n".join(paragraphs).strip()
    if not text:
        raise ResumeError("Word 파일에서 읽어낼 내용이 없습니다")
    return text


def _is_docx(raw: bytes) -> bool:
    if not raw.startswith(ZIP_MAGIC):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return DOCX_BODY in z.namelist()
    except zipfile.BadZipFile:
        return False


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "cp949", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ResumeError("이력서 파일의 문자 인코딩을 알 수 없습니다")


def from_bytes(raw: bytes) -> Resume:
    """내려받은 바이트를 Resume으로 만든다.

    PDF는 그대로, DOCX는 텍스트를 뽑아서, 나머지는 텍스트로 읽어본다.
    한글(.hwp)은 아직 지원하지 않는다.
    """
    if not raw:
        raise ResumeError("이력서 파일이 비어 있습니다")
    if len(raw) > MAX_BYTES:
        raise ResumeError(
            f"이력서 파일이 너무 큽니다 ({len(raw) / 1024 / 1024:.1f}MB). "
            f"{MAX_BYTES // 1024 // 1024}MB 이하만 처리합니다"
        )

    if raw.startswith(PDF_MAGIC):
        return Resume(pdf_bytes=raw)

    if _is_docx(raw):
        return Resume(text=_docx_text(raw))

    # 그 외 ZIP·OLE 계열은 텍스트로 읽으면 깨진 글자가 프롬프트에 들어간다.
    if raw.startswith(ZIP_MAGIC) or raw.startswith(OLE_MAGIC):
        raise ResumeError(
            "PDF · Word · 텍스트 파일만 처리합니다. "
            "한글(.hwp)은 PDF로 변환해 주세요"
        )

    text = _decode_text(raw).strip()
    if not text:
        raise ResumeError("이력서에서 읽어낼 내용이 없습니다")
    return Resume(text=text)


def fetch(resume_file_url: str) -> Resume:
    """presigned URL에서 이력서를 내려받는다."""
    if not resume_file_url:
        raise ResumeError("resume_file_url이 비어 있습니다")

    try:
        with httpx2.Client(timeout=DOWNLOAD_TIMEOUT_SEC, follow_redirects=True) as client:
            response = client.get(resume_file_url)
            response.raise_for_status()
            raw = response.content
    except httpx2.HTTPStatusError as e:
        # presigned URL 만료가 가장 흔하다
        raise ResumeError(
            f"이력서를 내려받지 못했습니다 (HTTP {e.response.status_code}). "
            "presigned URL이 만료되었을 수 있습니다"
        ) from e
    except httpx2.HTTPError as e:
        raise ResumeError(f"이력서를 내려받지 못했습니다: {type(e).__name__}") from e

    return from_bytes(raw)
