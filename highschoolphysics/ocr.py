"""OCR adapters for local PaddleOCR scans."""


class OCRAdapterError(RuntimeError):
    pass


def _bbox_from_points(points):
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return [
        int(min(xs)),
        int(min(ys)),
        int(max(xs)),
        int(max(ys)),
    ]


def _iter_paddle_entries(raw_result):
    if raw_result is None:
        return
    if isinstance(raw_result, dict):
        yield raw_result
        return
    for entry in raw_result:
        if isinstance(entry, dict):
            yield entry
        elif (
            isinstance(entry, (list, tuple))
            and len(entry) == 2
            and isinstance(entry[1], (list, tuple))
        ):
            yield {
                "bbox": _bbox_from_points(entry[0]),
                "text": entry[1][0],
                "confidence": entry[1][1],
            }
        elif isinstance(entry, list):
            for nested in _iter_paddle_entries(entry):
                yield nested


def normalize_paddleocr_result(
    raw_result,
    source_path="",
    confidence_threshold=0.75,
):
    normalized = []
    for index, item in enumerate(_iter_paddle_entries(raw_result), start=1):
        confidence = float(item.get("confidence", item.get("score", 0)) or 0)
        review_status = "not_required"
        review_reason = ""
        if confidence < confidence_threshold:
            review_status = "required"
            review_reason = "low_confidence"
        normalized.append(
            {
                "item_index": index,
                "source_path": source_path,
                "text": str(item.get("text", "")),
                "confidence": confidence,
                "bbox": item.get("bbox") or [],
                "page_number": item.get("page_number") or 1,
                "student_id": item.get("student_id", ""),
                "question_id": item.get("question_id", ""),
                "review_status": review_status,
                "review_reason": review_reason,
                "raw": dict(item),
            }
        )
    return normalized


def run_paddleocr(image_paths, runner=None, confidence_threshold=0.75):
    image_paths = list(image_paths or [])
    if runner:
        raw_by_path = runner(image_paths)
    else:
        try:
            from paddleocr import PaddleOCR
        except Exception as error:
            raise OCRAdapterError("PaddleOCR package is not importable") from error
        engine = PaddleOCR(use_angle_cls=True, lang="ch")
        raw_by_path = {}
        for path in image_paths:
            if hasattr(engine, "ocr"):
                raw_by_path[path] = engine.ocr(path, cls=True)
            else:
                raw_by_path[path] = engine.predict(path)
    if isinstance(raw_by_path, dict):
        pairs = raw_by_path.items()
    else:
        pairs = zip(image_paths, raw_by_path)
    normalized = []
    for source_path, raw_result in pairs:
        normalized.extend(
            normalize_paddleocr_result(
                raw_result,
                source_path=source_path,
                confidence_threshold=confidence_threshold,
            )
        )
    return normalized
