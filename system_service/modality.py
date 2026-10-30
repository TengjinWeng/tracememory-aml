"""Conservative query intent for retrieving source image evidence."""
import re


def wants_image_evidence(text, has_query_image=False):
    # A request to explain an image or find text about one is not an explicit
    # request to retrieve its original pixels. Negations win over positive cues.
    if re.search(r"\b(?:no|without)\s+(?:any\s+)?(?:images?|photos?|pictures?)\b|"
                 r"\btext[- ]only\b|\b(?:don't|do not|not)\b.{0,30}\b(?:images?|photos?|pictures?|photographs?)\b|"
                 r"(?:不要|不需要|不用|排除).{0,15}(?:照片|图片|图像)|只[要看].{0,8}(?:文字|文本)", text, re.I):
        return False
    if has_query_image:
        return True
    return bool(re.search(r"\b(?:find|show|retrieve|locate|get|return)\b.{0,100}\b(?:images?|pictures?|photos?|photographs?)\b|"
                          r"(?:找|查找|检索|给我看|哪一?张|哪些).{0,40}(?:图片|照片|图像|原图)", text, re.I))


def prefer_image_evidence(fused, dense, docs):
    """Resolve close semantic matches in favor of requested image sources.

    Keep all text candidates. Weak or unrelated images get no preference, and
    exact identifiers/pixel matches retain their larger calibrated weights.
    """
    best = max(dense.values(), default=0.)
    if best <= 0:
        return
    for identity in fused:
        score = dense.get(identity, 0.)
        if docs[identity]['pixels'] and score >= max(.3, .75 * best):
            fused[identity] += .06 * score / best
