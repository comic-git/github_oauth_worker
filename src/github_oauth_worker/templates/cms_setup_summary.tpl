<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Review CMS migration</title></head>
<body><main><h1>Review CMS migration</h1>
<p>{{ repository.owner.login }}/{{ repository.name }}</p>
<p>Target branch: {{ preview.target_branch }}</p>
<p>Engine selector: {{ preview.engine_selector }}</p>
<p>Resolved engine commit: {{ preview.engine_commit_sha }}</p>
{% if access_policy_plan %}
<h2>CMS website access</h2>
<p>Policy on <code>{{ access_policy_plan.default_branch }}</code>: {{ access_policy_plan.change.value }}.</p>
{% if existing_policy_pull_request_url %}<p>Existing access-policy pull request: <a href="{{ existing_policy_pull_request_url }}">Review on GitHub</a></p>{% endif %}
<p>Worker: <code>{{ access_policy_plan.desired_policy.worker_origin }}</code></p>
<ul>{% for origin in access_policy_plan.desired_policy.authorized_origins %}<li><code>{{ origin }}</code></li>{% endfor %}</ul>
{% if access_policy_plan.needs_separate_pull_request %}<p>A separate access-policy pull request will target the default branch.</p>{% elif access_policy_plan.change.value != 'unchanged' %}<p>The access policy will be included in this pull request.</p>{% endif %}
{% endif %}
{% if preview.already_enabled %}
<p>CMS is already enabled on the selected branch. Only website access approval needs a pull request.</p>
{% elif existing_pull_request_url %}
<p>Existing pull request: <a href="{{ existing_pull_request_url }}">Review on GitHub</a></p>
<h2>Changes to the pull request</h2>
{% else %}
<h2>Proposed files</h2>
{% endif %}
{% if superseded_pull_requests %}
<p>After this pull request is ready, the following older CMS migration pull requests will be closed. Their branches will be deleted if no open pull request still uses them:</p>
<ul>{% for pull_request in superseded_pull_requests %}<li><a href="{{ pull_request.html_url }}">Pull request #{{ pull_request.number }}</a></li>{% endfor %}</ul>
{% endif %}
{% if preview.already_enabled %}<p>The policy file will be {{ access_policy_plan.change.value }}.</p>
{% elif up_to_date %}<p>The migration pull request is up to date.</p>
{% elif changes %}
<table><thead><tr><th scope="col">File</th><th scope="col">Change</th></tr></thead>
<tbody>{% for change in changes %}<tr><td>{{ change.path }}</td><td>{{ change.kind.value|capitalize }}</td></tr>{% endfor %}</tbody></table>
{% else %}<p>{% if superseded_pull_requests %}No file changes. Older migration pull requests will be closed and their unused branches deleted.{% elif existing_pull_request_url %}No file changes. The pull request's recorded engine commit will be refreshed.{% else %}No migration file changes are needed.{% endif %}</p>{% endif %}
{% if not up_to_date %}<form action="/setup/confirm" method="post"><p><input type="hidden" name="state" value="{{ state_token }}"><button type="submit">{% if preview.already_enabled %}{% if existing_policy_pull_request_url %}Update{% else %}Create{% endif %} access-policy pull request{% elif access_policy_plan and access_policy_plan.needs_separate_pull_request %}Prepare CMS pull requests{% elif existing_pull_request_url %}Update migration pull request{% else %}Create migration pull request{% endif %}</button></p></form>{% endif %}
<form action="/setup/switch-account" method="post"><p><button type="submit">Use another GitHub account</button></p></form>
</main></body></html>
