"""Parse explicitly labelled account free-usage model lists conservatively."""

import re
from html.parser import HTMLParser


_FREE_USAGE_INLINE_MARKERS = (
    "free usage covers",
    "free usage credits can be used with the following cloud models",
    "бесплатное использование распространяется",
)


class _FreeModelsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.marker = False
        self.inline_marker = False
        self.list_depth = 0
        self.completed = False
        self.anchor_depth = 0
        self.model_text = []
        self.models = []
        self.inline_anchor_depth = 0
        self.inline_model_text: list[str] = []
        self.inline_models: list[str] = []
        self.section_depth = 0
        self.section_seen = 0
        self.quota = []
        self.visible_text = []
        self.ignore_depth = 0
        self.balance_depth = 0
        self.balance_seen = 0
        self.balance_text = []
        self.auto_reload = []

    def uses_inline_free_usage_format(self) -> bool:
        return self.section_seen == 1 and self.inline_marker and bool(self.inline_models)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get('id') == 'extra-usage-balance':
            self.balance_seen += 1
            self.balance_depth = 1
        elif self.balance_depth and tag not in ('img', 'input', 'br', 'hr', 'meta', 'link'):
            self.balance_depth += 1
        if attributes.get('id') == 'extra-usage-reload-toggle':
            self.auto_reload.append(attributes.get('aria-checked'))
        if tag in ('script', 'style'):
            self.ignore_depth += 1
        if attributes.get('id') == 'free-plan-models':
            self.section_seen += 1
            self.section_depth = 1
        elif self.section_depth and tag not in ('img', 'input', 'br', 'hr', 'meta', 'link'):
            self.section_depth += 1
        usage_label = attributes.get('aria-label', '')
        usage = re.fullmatch(r'Free usage:\s*(\d+(?:\.\d+)?)%', usage_label, re.IGNORECASE)
        if usage:
            self.quota.append(float(usage.group(1)))
        if tag == 'ul' and self.marker and not self.completed:
            self.list_depth += 1
        if self.list_depth and tag == 'a':
            self.anchor_depth += 1
            if self.anchor_depth == 1:
                self.model_text = []
        if tag == 'a' and self.section_depth and self.inline_marker and not self.list_depth:
            href = attributes.get('href', '')
            if href.startswith('/library/'):
                self.inline_anchor_depth += 1
                if self.inline_anchor_depth == 1:
                    self.inline_model_text = []

    def handle_endtag(self, tag):
        if self.inline_anchor_depth and tag == 'a':
            self.inline_anchor_depth -= 1
            if self.inline_anchor_depth == 0:
                name = ''.join(self.inline_model_text).strip()
                if name:
                    self.inline_models.append(name)
        if self.list_depth and tag == 'a' and self.anchor_depth:
            self.anchor_depth -= 1
            if self.anchor_depth == 0:
                self.models.append(''.join(self.model_text).strip())
        if self.list_depth and tag == 'ul':
            self.list_depth -= 1
            if not self.list_depth:
                self.completed = True
        if self.section_depth and tag not in ('img', 'input', 'br', 'hr', 'meta', 'link'):
            self.section_depth -= 1
        if self.balance_depth and tag not in ('img', 'input', 'br', 'hr', 'meta', 'link'):
            self.balance_depth -= 1
        if tag in ('script', 'style') and self.ignore_depth:
            self.ignore_depth -= 1

    def handle_data(self, data):
        if self.section_depth:
            lowered = data.lower()
            if any(marker in lowered for marker in _FREE_USAGE_INLINE_MARKERS):
                if "free usage covers" in lowered or "бесплатное использование" in lowered:
                    self.inline_marker = True
                else:
                    self.marker = True
        if self.anchor_depth:
            self.model_text.append(data)
        if self.inline_anchor_depth:
            self.inline_model_text.append(data)
        if not self.ignore_depth:
            self.visible_text.append(data)
        if self.balance_depth:
            self.balance_text.append(data)


def _validate_model_names(models: list[str]) -> list[str]:
    if not models:
        raise ValueError('Free usage model list missing or unrecognized')
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model) for model in models):
        raise ValueError('Free usage model list contains unrecognized names')
    if len(set(models)) != len(models):
        raise ValueError('Free usage model list contains duplicates')
    return models


def parse_free_usage_model_list(html: str) -> list[str]:
    """Return a marked list of valid model names or fail closed on unknown markup."""
    parser = _FreeModelsParser()
    parser.feed(html)
    if parser.section_seen != 1:
        raise ValueError('Free usage model list missing or unrecognized')
    if parser.uses_inline_free_usage_format():
        return _validate_model_names(parser.inline_models)
    if not parser.marker or not parser.completed or not parser.models:
        raise ValueError('Free usage model list missing or unrecognized')
    return _validate_model_names(parser.models)


def _exclude_retiring_models(models: list[str], text: str) -> list[str]:
    lowered = text.lower()
    retiring: set[str] = set()
    block_markers = (
        'the following models will be retired soon',
        'следующие модели будут сняты с производства',
    )
    for marker in block_markers:
        pos = lowered.find(marker)
        if pos < 0:
            continue
        after = text[pos + len(marker):]
        free_usage_pos = after.lower().find('free usage')
        if free_usage_pos >= 0:
            after = after[:free_usage_pos]
        retiring.update(
            name for name in models
            if re.search(r'(?<![\w:.-])' + re.escape(name) + r'(?![\w:.-])', after)
        )
        if not retiring:
            raise ValueError('Retirement warning could not be matched to a free model')
        break

    single_patterns = (
        re.compile(
            r'(?:model\s+)?([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})\s+will be discontinued',
            re.IGNORECASE,
        ),
        re.compile(
            r'модель\s+([A-Za-z0-9][A-Za-z0-9._:/-]{0,127})\s+будет снята с производства',
            re.IGNORECASE,
        ),
    )
    for pattern in single_patterns:
        for match in pattern.finditer(text):
            retiring.add(match.group(1))

    filtered = [name for name in models if name not in retiring]
    if not filtered:
        raise ValueError('No free models remain after excluding retiring models')
    return filtered


def parse_free_usage_entitlement(html: str) -> tuple[list[str], float]:
    """Extract entitlement and exclude any model explicitly listed for retirement."""
    parser = _FreeModelsParser()
    parser.feed(html)
    models = parse_free_usage_model_list(html)
    text = ' '.join(' '.join(parser.visible_text).split())
    models = _exclude_retiring_models(models, text)

    if parser.uses_inline_free_usage_format():
        if parser.balance_seen == 1 and (
            ''.join(parser.balance_text).strip() not in ('$0', '$0.00')
            or parser.auto_reload not in ([], ['false'])
        ):
            raise ValueError('Paid balance or auto-reload cannot be ruled out')
        if len(parser.quota) == 1 and not 0 <= parser.quota[0] < 100:
            raise ValueError('Free usage quota missing, ambiguous or exhausted')
        quota = parser.quota[0] if len(parser.quota) == 1 else 0.0
        return models, quota

    if len(parser.quota) != 1 or not 0 <= parser.quota[0] < 100:
        raise ValueError('Free usage quota missing, ambiguous or exhausted')
    if parser.balance_seen != 1 or ''.join(parser.balance_text).strip() not in ('$0', '$0.00') or parser.auto_reload != ['false']:
        raise ValueError('Paid balance or auto-reload cannot be ruled out')
    return models, parser.quota[0]


def settings_omits_free_plan(html: str) -> bool:
    """Authenticated settings page that no longer publishes a free-model list."""

    class Markers(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.free = 0
            self.account = 0
            self.usage = 0

        def handle_starttag(self, tag, attrs):
            element_id = dict(attrs).get('id')
            if element_id == 'free-plan-models':
                self.free += 1
            elif element_id == 'settings-account-menu':
                self.account += 1
            elif element_id == 'usage-cost-report':
                self.usage += 1

    parser = Markers()
    parser.feed(html)
    return parser.free == 0 and parser.account == 1 and parser.usage == 1


def parse_settings_account_email(html: str) -> str:
    class AccountMenu(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.found = 0
            self.text = []

        def handle_starttag(self, tag, attrs):
            if dict(attrs).get('id') == 'settings-account-menu':
                self.found += 1
                self.depth = 1
            elif self.depth and tag not in ('img', 'input', 'br', 'hr', 'meta', 'link'):
                self.depth += 1

        def handle_endtag(self, tag):
            if self.depth and tag not in ('img', 'input', 'br', 'hr', 'meta', 'link'):
                self.depth -= 1

        def handle_data(self, data):
            if self.depth:
                self.text.append(data)

    parser = AccountMenu()
    parser.feed(html)
    emails = set(re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', ' '.join(parser.text).lower()))
    if parser.found != 1 or len(emails) != 1:
        raise ValueError('Settings account identity missing or ambiguous')
    return emails.pop()
