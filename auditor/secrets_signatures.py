"""A comprehensive catalog of committed-secret signatures — provider API keys, tokens, and
private keys with distinctive, self-identifying formats (prefix + length/structure), drawn from
the canonical gitleaks / trufflehog / detect-secrets rule families.

Only **high-confidence, format-specific** patterns live here: each is anchored enough that a
match is almost certainly a real credential, so detection needs no surrounding context (which is
why it slots into the cross-language string-literal sweep). Generic ``api_key = "…"`` keyword
heuristics are intentionally left to the security category's context-aware rule — adding them
here would flood the sweep with false positives.

The whole catalog compiles to ONE regex with a named group per provider, so a literal is scanned
in a single pass and ``scan()`` returns the human name of whatever matched.
"""

import re

# (slug, human name, pattern). ``slug`` must be a valid, unique regex group name (no hyphens).
# Patterns use only non-capturing groups internally so the named group stays unambiguous.
_PATTERNS: list[tuple[str, str, str]] = [
    # --- private keys & generic tokens ---------------------------------------
    (
        "private_key",
        "private key (PEM)",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----",
    ),
    (
        "jwt",
        "JSON Web Token",
        r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    ),
    # --- AWS -----------------------------------------------------------------
    (
        "aws_access_key",
        "AWS access key ID",
        r"(?:A3T[A-Z0-9]|AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ABIA|ACCA)[A-Z0-9]{16}",
    ),
    # --- Google / GCP --------------------------------------------------------
    ("gcp_api_key", "Google API key", r"AIza[0-9A-Za-z_-]{35}"),
    (
        "gcp_oauth",
        "Google OAuth client ID",
        r"[0-9]+-[0-9A-Za-z_]{32}\.apps\.googleusercontent\.com",
    ),
    ("gcp_sa", "GCP service-account key", r'"type"\s*:\s*"service_account"'),
    (
        "firebase_fcm",
        "Firebase Cloud Messaging key",
        r"AAAA[A-Za-z0-9_-]{7}:APA91[A-Za-z0-9_-]{130,}",
    ),
    # --- GitHub / GitLab / VCS ----------------------------------------------
    ("github_pat", "GitHub personal access token", r"gh[pousr]_[0-9A-Za-z]{36}"),
    ("github_fine", "GitHub fine-grained PAT", r"github_pat_[0-9A-Za-z_]{82}"),
    ("gitlab_pat", "GitLab personal access token", r"glpat-[0-9A-Za-z_-]{20}"),
    (
        "gitlab_runner",
        "GitLab runner registration token",
        r"GR1348941[0-9A-Za-z_-]{20}",
    ),
    # --- package registries --------------------------------------------------
    ("npm_token", "npm access token", r"npm_[0-9A-Za-z]{36}"),
    ("pypi_token", "PyPI upload token", r"pypi-AgEIcHlwaS5vcmc[0-9A-Za-z_-]{50,}"),
    ("rubygems", "RubyGems API key", r"rubygems_[0-9a-f]{48}"),
    ("dockerhub_pat", "Docker Hub PAT", r"dckr_pat_[0-9A-Za-z_-]{27}"),
    # --- AI / ML providers ---------------------------------------------------
    (
        "openai",
        "OpenAI API key",
        r"sk-(?:proj-)?[A-Za-z0-9_-]{20}T3BlbkFJ[A-Za-z0-9_-]{20}",
    ),
    ("anthropic", "Anthropic API key", r"sk-ant-(?:api03|admin01)-[0-9A-Za-z_-]{80,}"),
    ("huggingface", "Hugging Face token", r"hf_[0-9A-Za-z]{34}"),
    ("replicate", "Replicate API token", r"r8_[0-9A-Za-z]{37}"),
    ("cohere", "Cohere API key", r"co_[A-Za-z0-9]{40}"),
    # --- comms / chat --------------------------------------------------------
    (
        "slack_token",
        "Slack token",
        r"xox[baprse]-(?:[0-9A-Za-z]{10,48}|[0-9]{10,13}-[0-9]{10,13}-[0-9A-Za-z]{24,})",
    ),
    (
        "slack_webhook",
        "Slack webhook URL",
        r"https://hooks\.slack\.com/services/T[0-9A-Za-z_]+/B[0-9A-Za-z_]+/[0-9A-Za-z_]{20,}",
    ),
    (
        "discord_webhook",
        "Discord webhook URL",
        r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[0-9]{17,20}/[0-9A-Za-z_-]{60,}",
    ),
    (
        "discord_token",
        "Discord bot token",
        r"[MNO][A-Za-z0-9_-]{23,25}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}",
    ),
    ("telegram", "Telegram bot token", r"[0-9]{8,10}:AA[A-Za-z0-9_-]{32,35}"),
    ("twilio", "Twilio API key", r"SK[0-9a-fA-F]{32}"),
    ("sendgrid", "SendGrid API key", r"SG\.[0-9A-Za-z_-]{22}\.[0-9A-Za-z_-]{43}"),
    ("mailgun", "Mailgun API key", r"key-[0-9a-zA-Z]{32}"),
    # Mailchimp keys are `<32 hex>-us<n>` — format-identical to an md5 + "-us1" cache key, so it
    # can't meet the "almost certainly a real credential" bar this catalog requires. Dropped.
    # --- payments ------------------------------------------------------------
    (
        "stripe",
        "Stripe secret/restricted key",
        r"(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,}",
    ),
    ("stripe_whsec", "Stripe webhook signing secret", r"whsec_[0-9A-Za-z]{32,}"),
    ("square_access", "Square access token", r"sq0atp-[0-9A-Za-z_-]{22}"),
    ("square_secret", "Square OAuth secret", r"sq0csp-[0-9A-Za-z_-]{43}"),
    ("square_prod", "Square production token", r"EAAA[0-9A-Za-z_-]{60}"),
    (
        "paypal_braintree",
        "PayPal/Braintree access token",
        r"access_token\$production\$[0-9a-z]{16}\$[0-9a-f]{32}",
    ),
    ("shopify", "Shopify access token", r"shp(?:at|ca|pa|ss)_[0-9a-fA-F]{32}"),
    # --- cloud / infra -------------------------------------------------------
    ("digitalocean", "DigitalOcean token", r"do[oprv]_v1_[0-9a-f]{64}"),
    ("cloudflare_api", "Cloudflare API token", r"v1\.0-[0-9a-f]{8}-[0-9a-f]{72}"),
    ("databricks", "Databricks PAT", r"dapi[0-9a-fA-F]{32}(?:-[0-9]+)?"),
    ("doppler", "Doppler token", r"dp\.(?:pt|st|ct|sa|scim|audit)\.[0-9A-Za-z]{40,44}"),
    (
        "vault",
        "HashiCorp Vault token",
        r"(?:hvs\.[0-9A-Za-z_-]{90,}|s\.[0-9A-Za-z]{24})",
    ),
    (
        "terraform_cloud",
        "Terraform Cloud token",
        r"[0-9A-Za-z]{14}\.atlasv1\.[0-9A-Za-z_=-]{60,}",
    ),
    ("grafana", "Grafana service-account token", r"gl(?:sa|c)_[0-9A-Za-z_]{32,}"),
    ("okta", "Okta API token", r"00[0-9A-Za-z_-]{40}"),
    ("newrelic", "New Relic key", r"NR(?:AK|JS|II|RA)-[0-9A-Za-z_]{27}"),
    ("sentry", "Sentry auth token", r"sntrys_[0-9A-Za-z_=]{60,}"),
    ("atlassian", "Atlassian/Jira API token", r"ATATT3[0-9A-Za-z_=-]{180,}"),
    # --- SaaS / productivity -------------------------------------------------
    (
        "notion",
        "Notion integration token",
        r"(?:secret_[0-9A-Za-z]{43}|ntn_[0-9A-Za-z]{46})",
    ),
    ("airtable", "Airtable token", r"pat[0-9A-Za-z]{14}\.[0-9a-f]{64}"),
    ("linear", "Linear API key", r"lin_api_[0-9A-Za-z]{40}"),
    ("figma", "Figma personal access token", r"figd_[0-9A-Za-z_-]{40,}"),
    ("dropbox", "Dropbox access token", r"sl\.[0-9A-Za-z_-]{130,}"),
    ("asana", "Asana PAT", r"1/[0-9]{16}:[0-9a-f]{32}"),
    (
        "mapbox_secret",
        "Mapbox secret token",
        r"sk\.eyJ[0-9A-Za-z_-]{20,}\.[0-9A-Za-z_-]{20,}",
    ),
    # --- social --------------------------------------------------------------
    ("facebook", "Facebook access token", r"EAACEdEose0cBA[0-9A-Za-z]+"),
    ("twitter_bearer", "Twitter/X bearer token", r"AAAAAAAAAA[0-9A-Za-z%]{50,}"),
    # --- more cloud providers (historic) -------------------------------------
    (
        "azure_storage_key",
        "Azure storage account key",
        r"AccountKey=[0-9A-Za-z+/]{86}==",
    ),
    ("alibaba_akid", "Alibaba Cloud AccessKey ID", r"LTAI[0-9A-Za-z]{20}"),
    ("tencent_secretid", "Tencent Cloud secret ID", r"AKID[0-9A-Za-z]{32,}"),
    (
        "aws_mws",
        "Amazon MWS auth token",
        r"amzn\.mws\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    ),
    ("slack_app", "Slack app-level token", r"xapp-[0-9]-[A-Z0-9]+-[0-9]+-[0-9a-f]+"),
    # Twilio account SID (`AC<32 hex>`) is a *public* identifier, not a secret — removed.
    # --- modern AI / ML providers (newly popular) ----------------------------
    ("groq", "Groq API key", r"gsk_[0-9A-Za-z]{52}"),
    ("perplexity", "Perplexity API key", r"pplx-[0-9A-Za-z]{40,}"),
    ("fireworks", "Fireworks AI key", r"fw_[0-9A-Za-z]{20,}"),
    ("langsmith", "LangSmith API key", r"lsv2_(?:pt|sk)_[0-9a-f]{32}_[0-9a-f]{10}"),
    # --- modern PaaS / infra (newly popular) ---------------------------------
    ("planetscale_pw", "PlanetScale password", r"pscale_pw_[0-9A-Za-z._-]{32,}"),
    ("planetscale_tkn", "PlanetScale token", r"pscale_tkn_[0-9A-Za-z._-]{32,}"),
    (
        "planetscale_oauth",
        "PlanetScale OAuth token",
        r"pscale_oauth_[0-9A-Za-z._-]{32,}",
    ),
    ("supabase", "Supabase service token", r"sbp_[0-9a-f]{40}"),
    ("render", "Render API key", r"rnd_[0-9A-Za-z]{27}"),
    ("flyio", "Fly.io API token", r"fo1_[0-9A-Za-z_-]{43}"),
    ("netlify", "Netlify PAT", r"nfp_[0-9A-Za-z]{36,}"),
    (
        "tailscale",
        "Tailscale auth key",
        r"tskey-(?:auth|api|client)-[0-9A-Za-z]+-[0-9A-Za-z]+",
    ),
    ("pulumi", "Pulumi access token", r"pul-[0-9a-f]{40}"),
    ("dynatrace", "Dynatrace token", r"dt0c01\.[A-Z0-9]{24}\.[A-Z0-9]{64}"),
    ("artifactory", "JFrog Artifactory token", r"AKCp8[0-9A-Za-z]{69,}"),
    ("clojars", "Clojars deploy token", r"CLOJARS_[0-9a-z]{60}"),
    ("age_secret", "age secret key", r"AGE-SECRET-KEY-1[0-9A-Z]{58}"),
    ("sonarqube", "SonarQube token", r"sq[apu]_[0-9a-f]{40}"),
    # --- dev tools / CI / observability ---------------------------------------
    ("postman", "Postman API key", r"PMAK-[0-9a-f]{24}-[0-9a-f]{34}"),
    ("prefect", "Prefect API key", r"pn[ua]_[0-9A-Za-z]{36}"),
    ("readme", "ReadMe API key", r"rdme_[0-9a-z]{70}"),
    ("contentful", "Contentful PAT", r"CFPAT-[0-9A-Za-z_-]{43}"),
    ("typeform", "Typeform PAT", r"tfp_[0-9A-Za-z._-]{59}"),
    ("frameio", "Frame.io token", r"fio-u-[0-9A-Za-z_-]{64}"),
    ("clickup", "ClickUp token", r"pk_[0-9]{7,8}_[0-9A-Z]{32}"),
    ("circleci", "CircleCI PAT", r"CCIPAT_[0-9A-Za-z]{22}_[0-9a-f]{40}"),
    (
        "launchdarkly",
        "LaunchDarkly key",
        r"(?:api|sdk|mob)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    ),
    # --- more payments / commerce --------------------------------------------
    (
        "plaid",
        "Plaid access token",
        r"access-(?:sandbox|development|production)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    ),
    ("razorpay", "Razorpay key ID", r"rzp_(?:live|test)_[0-9A-Za-z]{14}"),
    ("flutterwave", "Flutterwave secret key", r"FLWSECK_TEST-[0-9a-f]{12}-X"),
    ("easypost", "EasyPost API key", r"EZ[AT]K[0-9A-Za-z]{54}"),
    ("duffel", "Duffel API token", r"duffel_(?:test|live)_[0-9A-Za-z_-]{43}"),
    ("shippo", "Shippo API token", r"shippo_(?:live|test)_[0-9a-f]{40}"),
    # --- more comms / CRM -----------------------------------------------------
    (
        "hubspot",
        "HubSpot private app token",
        r"pat-(?:na|eu)1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    ),
    ("sendinblue", "Sendinblue/Brevo API key", r"xkeysib-[0-9a-f]{64}-[0-9A-Za-z]{16}"),
    # --- connection strings with embedded password --------------------------
    (
        "db_uri",
        "database URI with password",
        r"(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|rediss)://[^:@/\s]+:[^@/\s]{3,}@[^\s/'\"]+",
    ),
]

_COMBINED = re.compile(
    "|".join(f"(?P<{slug}>{pattern})" for slug, _name, pattern in _PATTERNS)
)
_NAMES: dict[str, str] = {slug: name for slug, name, _ in _PATTERNS}

#: a literal must be at least this long to possibly hold any catalogued secret (cheap prefilter)
_MIN_LEN = 16


def scan(text: str) -> str | None:
    """Human name of the first catalogued secret found in ``text``, else ``None``."""
    if len(text) < _MIN_LEN:
        return None
    m = _COMBINED.search(text)
    return _NAMES[m.lastgroup] if m and m.lastgroup else None
