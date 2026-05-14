{# Relatio template -- Jinja2. Rendered by render.py against PipelineState. #}
{# Canonical section order per about-advocatus.md: verdict first, then      #}
{# objections, probationes, tabula fontium, audit trail.                    #}
# Relatio: {{ paper_id }}

**{{ paper_title }}**

> *The Advocatus Diaboli is called to examine the cause of {{ paper_id }}.*
> *Authors: {{ authors | join(', ') }}. Audience: {{ audience }}.*
> *The tribunal convenes.*

---
{% if seal == "sine_causa" %}

## The Seal

***Sine causa.***

The tribunal does not convene. The paper contains no claims to examine.
{% else %}

## The Seal

***{{ seal | seal_label }}.***

{{ assessment | sanitize_md }}

**Confidence:** {{ "%.2f" | format(confidence) }}
{% if objections %}

## Objections
{% for obj in objections %}

### Objection {{ loop.index }} (Severity: {{ obj.severity | capitalize }}) - {{ obj.gravamen | truncate(80) }}

**Quoted text:**

> {{ obj.quoted_text | sanitize_md }}

**The gravamen:** {{ obj.gravamen | sanitize_md }}

**Failed test:** *{{ obj.failed_test | capitalize }}.* {{ obj.contradicting_evidence | sanitize_md }}

**Motivatio:**

- *Adversary:* {{ obj.adversary | sanitize_md }}
- *Forum:* {{ obj.forum | forum_label }}
- *Damage:* {{ obj.damage | damage_label }}

{{ obj.explanation | sanitize_md }}
{% endfor %}
{% endif %}
{% if probationes %}

## Probationes
{% for p in probationes %}

### {{ p.section }}: {{ p.charge_summary | truncate(80) }}

*Probatio.* {{ p.explanation | sanitize_md }} The Defensor prevailed on the *{{ p.killing_challenge | challenge_label }}* challenge.
{% endfor %}
{% endif %}
{% if notae_minores %}

## Notae Minores
{% for n in notae_minores %}
- {{ n.text | sanitize_md }}
{% endfor %}
{% endif %}
{% if tabula_fontium %}

## Tabula Fontium

| Paper | Resolved | Quote Match | Discrepancy |
|---|---|---|---|
{% for t in tabula_fontium %}
| {{ t.paper_id }} | {{ "Yes" if t.resolved else "No" }} | {{ t.quote_match }} | {{ t.discrepancy | sanitize_md if t.discrepancy else "-" }} |
{% endfor %}
{% endif %}

## Acta

- {{ n_articuli }} articuli examined.
- {{ n_candidates }} candidate charges filed.
- Defensor cross-examination: {{ n_killed }} killed, {{ n_relegated }} relegated, {{ n_survived }} survived.
- {{ n_objections }} objections in the final record after motivatio review.
{% if kill_attribution %}- Kill attribution by challenge: {{ kill_attribution }}.{% endif %}
{% endif %}
