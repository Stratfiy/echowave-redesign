"""The script tag that puts an agent on a web page.

Lives in services because two things write it: the embed screen
(``routes/workflow_embed.py``) and Studio, which writes it into the sites it
builds (``services/studio/sites.py``).
"""

from api.constants import BACKEND_API_ENDPOINT, ENVIRONMENT, UI_APP_URL
from api.db.models import EmbedTokenModel


def generate_embed_script(token: EmbedTokenModel) -> str:
    """Generate the embed script for a given token.

    The text-chat flag rides in the script URL rather than the config the
    widget fetches, because the widget reads it while deciding what to build
    and that happens before the fetch returns. Sending it both ways would give
    two answers that can disagree; the URL is the one that arrives in time.
    """
    base_url = str(UI_APP_URL).rstrip("/")
    settings = token.settings or {}

    # Off unless asked for. The widget has supported a typed conversation since
    # it was written, but only ever when someone hand-edited `text=true` into
    # the script tag -- which no screen mentioned and therefore nobody did. The
    # capability was shipped and invisible; this is the switch it never had.
    text_param = "&text=true" if settings.get("enableText") is True else ""

    return f"""<!-- Decibyl Voice Widget -->
<script>
  (function(d, s, id) {{
    var js, fjs = d.getElementsByTagName(s)[0];
    if (d.getElementById(id)) return;
    js = d.createElement(s); js.id = id;
    js.src = '{base_url}/embed/decibyl-widget.js?token={token.token}&environment={ENVIRONMENT}&apiEndpoint={BACKEND_API_ENDPOINT}{text_param}';
    js.async = true;
    fjs.parentNode.insertBefore(js, fjs);
  }}(document, 'script', 'decibyl-widget'));
</script>"""
