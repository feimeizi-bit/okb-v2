"""Read-only, bounded full UTF-8 capture for an explicitly declared file scope.

This proves neither the live Agent's instance binding nor coverage of other
storage. No probe markers are written, and no filesystem errors become empty.
"""
import hashlib
import os
import stat
from pathlib import Path

VERSION = 'okb-memory-file-capture-v1-draft'
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


class MemoryCaptureError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _signature(st):
    # Windows path-stat and handle-stat can expose different ctime semantics.
    # Compare identity/size/mtime across both; keep ctime for same-API checks below.
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def _plain(path):
    # Reject symlinks and Windows reparse points (including junctions).
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
        raise MemoryCaptureError('memory scope contains a link/reparse point')
    return st


def _enumerate(root, patterns):
    if not stat.S_ISDIR(_plain(root).st_mode):
        raise MemoryCaptureError('memory root is not a directory')
    found = {}
    for pattern in patterns:
        parts = pattern.split('/')
        if '\\' in pattern or ':' in pattern or any(x in ('', '.', '..') for x in parts):
            raise MemoryCaptureError('invalid declared memory path')
        if len(parts) == 1 and '*' not in pattern and '?' not in pattern:
            candidates = [root / pattern]
        elif len(parts) == 2 and parts[1] == '*.md' and '*' not in parts[0]:
            directory = root / parts[0]
            try:
                st = _plain(directory)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(st.st_mode):
                raise MemoryCaptureError('declared memory directory is not a directory')
            # iterdir propagates access errors; glob can suppress them.
            candidates = sorted(p for p in directory.iterdir() if p.name.endswith('.md'))
        else:
            raise MemoryCaptureError('unsupported declared memory pattern')
        for path in candidates:
            try:
                st = _plain(path)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(st.st_mode):
                raise MemoryCaptureError('declared memory item is not a regular file')
            name = path.relative_to(root).as_posix()
            if name in found:
                raise MemoryCaptureError('duplicate declared memory file')
            found[name] = _signature(st)
    return found


def capture_files(root, patterns, *, max_file_bytes=MAX_FILE_BYTES, max_total_bytes=MAX_TOTAL_BYTES):
    """Read each physical file once; preserve CRLF, BOM, headers and prose.

    The caller can derive historical parser input by universal-newline conversion.
    Metadata race checks detect ordinary changes; they are not an atomic filesystem
    snapshot or an adversarial tamper-proof guarantee.
    """
    root = Path(root)
    if type(max_file_bytes) is not int or type(max_total_bytes) is not int or min(max_file_bytes, max_total_bytes) < 1:
        raise MemoryCaptureError('invalid memory capture byte limit')
    try:
        _plain(root)
    except FileNotFoundError:
        return dict(version=VERSION, documents=[], enumeration_complete=False,
                    errors=['memory_root_missing'], total_source_bytes=0)
    before = _enumerate(root, patterns)
    documents, total = [], 0
    for name in sorted(before):
        path = root / name
        if before[name][2] > min(max_file_bytes, max_total_bytes - total):
            raise MemoryCaptureError('memory capture byte limit exceeded')
        with path.open('rb') as stream:
            first = os.fstat(stream.fileno())
            if _signature(first) != before[name]:
                raise MemoryCaptureError('memory file changed before read')
            data = stream.read(min(max_file_bytes, max_total_bytes - total) + 1)
            last = os.fstat(stream.fileno())
        if len(data) > max_file_bytes or total + len(data) > max_total_bytes:
            raise MemoryCaptureError('memory capture byte limit exceeded')
        if _signature(first) != _signature(last) or first.st_ctime_ns != last.st_ctime_ns or len(data) != last.st_size:
            raise MemoryCaptureError('memory file changed during read')
        try:
            content = data.decode('utf8', errors='strict')
        except UnicodeError as exc:
            raise MemoryCaptureError('memory is not valid UTF-8') from exc
        total += len(data)
        documents.append(dict(path=name, content=content, source_bytes=len(data),
                              source_bytes_sha256=digest(data), source_mtime_ns=last.st_mtime_ns))
    if before != _enumerate(root, patterns):
        raise MemoryCaptureError('memory scope changed during capture')
    return dict(version=VERSION, documents=documents, enumeration_complete=True,
                errors=[], total_source_bytes=total)


def redact_capture(capture, pii, evidence_id):
    """Serialize only PII-processed full text; source and stored hashes stay distinct."""
    documents, hits = [], []
    for i, doc in enumerate(capture['documents']):
        item = dict(doc)
        for field in ('path', 'content'):
            value, found = pii.process(item[field], f'payload.capture.documents[{i}].{field}',
                                       'memory', evidence_id)
            item[field] = value
            hits.extend(found)
        item['redacted'] = item['content'] != doc['content']
        data = item['content'].encode('utf8')
        item['stored_utf8_bytes'] = len(data)
        item['stored_utf8_sha256'] = digest(data)
        documents.append(item)
    # Context snippets can contain another declared PII. Sanitize before storage.
    for hit in hits:
        hit['context_excerpt'], _ = pii.process(hit['context_excerpt'], 'context_excerpt', 'memory', evidence_id)
    return dict(version=capture['version'], documents=documents,
                enumeration_complete=capture['enumeration_complete'], errors=list(capture['errors']),
                total_source_bytes=capture['total_source_bytes'],
                representation='full_utf8_text_after_declared_pii_v1',
                redaction='engine.redact.PiiPipeline'), hits
