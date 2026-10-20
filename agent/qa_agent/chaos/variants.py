"""Browser-only perturbations; production recovery never sees the rename map."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

RENAMES = {'From account': 'Source account', 'To account': 'Destination', 'Amount (USD)': 'Transfer amount',
           'Memo': 'Note', 'Transfer funds': 'Send money', 'Apply filters': 'Search', 'Clear filters': 'Reset',
           'Transactions': 'Activity', 'Log out': 'Sign out', 'Enter Demo': 'Start the demo'}
VARIANTS = {'labels', 'testids', 'slow', 'flaky'}


def install(context, variants: list[str]) -> None:
    script = r"""(() => {
      const names = NAMES;
      const scan = () => {
        if (STRIP) document.querySelectorAll('[data-testid]').forEach(e => e.removeAttribute('data-testid'));
        if (!LABELS) return;
        document.querySelectorAll('label,button,a,[aria-label]').forEach(e => {
          const aria = e.getAttribute('aria-label');
          if (names[aria]) e.setAttribute('aria-label', names[aria]);
          const walk = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
          while (walk.nextNode()) {
            const n = walk.currentNode, key = n.textContent.trim();
            if (n.parentElement.closest('option')) continue;
            if (names[key]) n.textContent = n.textContent.replace(key, names[key]);
          }
        });
      };
      const start = () => { scan(); new MutationObserver(scan).observe(document.documentElement,
        {subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['data-testid','aria-label']}); };
      if (document.documentElement) start(); else document.addEventListener('DOMContentLoaded', start);
    })();"""
    context.add_init_script(script.replace('NAMES', json.dumps(RENAMES)).replace('STRIP',
                            str('testids' in variants).lower()).replace('LABELS', str('labels' in variants).lower()))
    if 'flaky' in variants:
        seen: set[str] = set()

        def route(request_route):
            request = request_route.request
            path = urlsplit(request.url).path
            if request.method == 'GET' and path.startswith('/api/') and path not in seen:
                seen.add(path)
                request_route.fulfill(status=503, content_type='application/json',
                                      body='{"error":{"code":"transient","message":"Temporary failure"}}')
            else:
                request_route.continue_()
        context.route('**/api/**', route)
