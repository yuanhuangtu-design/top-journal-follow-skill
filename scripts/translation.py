"""Bounded translation attempts with a persistent, content-addressed cache."""
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request


def good(original, translated):
    if not original or not translated or translated == original:
        return False
    chinese = len(re.findall(r"[\u4e00-\u9fff]", translated))
    if chinese / max(1, len(translated)) < .25:
        return False
    if re.search(r"(?:[A-Za-z]{3,}[ ,;]+){7}", translated):
        return False
    # Guard against silently dropping or inventing numeric results.
    numbers = lambda s: sorted(re.findall(r"\d+(?:\.\d+)?", s))
    return numbers(original) == numbers(translated)


def chunks(text, max_bytes=450):
    # Prefer sentences, then whitespace; never split a word/UTF-8 character.
    result, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text.strip()):
        if not sentence:
            continue
        units = [sentence] if len(sentence.encode()) <= max_bytes else sentence.split()
        for unit in units:
            if len(unit.encode()) > max_bytes:
                raise ValueError("Unbreakable token exceeds translation limit")
            candidate = (current + " " + unit).strip()
            if len(candidate.encode()) > max_bytes:
                result.append(current)
                current = unit
            else:
                current = candidate
    if current:
        result.append(current)
    return result


class Translator:
    def __init__(self, cache, budget=120):
        self.cache = cache
        self.deadline = time.monotonic() + budget
        self.quota_exhausted = False

    def translate(self, text):
        if not text:
            return ""
        key = hashlib.sha256(("mymemory-zh-v2:" + text).encode()).hexdigest()
        cached = self.cache.get(key)
        if cached and good(text, cached):
            return cached
        if self.quota_exhausted:
            return ""
        parts = []
        try:
            for part in chunks(text):
                part_key = hashlib.sha256(("mymemory-zh-v2:" + part).encode()).hexdigest()
                if good(part, self.cache.get(part_key, "")):
                    parts.append(self.cache[part_key])
                    continue
                remaining = self.deadline - time.monotonic()
                if remaining <= 1:
                    return ""
                url = "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": part, "langpair": "en|zh-CN"})
                with urllib.request.urlopen(url, timeout=min(15, remaining)) as response:
                    data = json.loads(response.read().decode())
                if data.get("responseStatus") != 200:
                    self.quota_exhausted = True
                    return ""
                translated = data.get("responseData", {}).get("translatedText", "")
                if not good(part, translated):
                    return ""
                self.cache[part_key] = translated
                parts.append(translated)
            result = "\n".join(parts)
            if good(text, result):
                self.cache[key] = result
                return result
        except (OSError, ValueError, KeyError):
            pass
        return ""
