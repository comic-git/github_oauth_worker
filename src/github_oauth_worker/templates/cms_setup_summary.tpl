<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Review CMS migration</title></head>
<body><main><h1>Review CMS migration</h1>
<p>{{ repository.owner.login }}/{{ repository.name }}</p>
<p>Target branch: {{ preview.target_branch }}</p>
<p>Engine selector: {{ preview.engine_selector }}</p>
<p>Resolved engine commit: {{ preview.engine_commit_sha }}</p>
{% if existing_pull_request_url %}
<p>Existing pull request: <a href="{{ existing_pull_request_url }}">Review on GitHub</a></p>
<h2>Changes to the pull request</h2>
{% else %}
<h2>Proposed files</h2>
{% endif %}
{% if superseded_pull_requests %}
<p>After this pull request is ready, the following older CMS migration pull requests will be closed. Their branches will be deleted if no open pull request still uses them:</p>
<ul>{% for pull_request in superseded_pull_requests %}<li><a href="{{ pull_request.html_url }}">Pull request #{{ pull_request.number }}</a></li>{% endfor %}</ul>
{% endif %}
{% if up_to_date %}<p>The migration pull request is up to date.</p>
{% elif changes %}
<table><thead><tr><th scope="col">File</th><th scope="col">Change</th></tr></thead>
<tbody>{% for change in changes %}<tr><td>{{ change.path }}</td><td>{{ change.kind.value|capitalize }}</td></tr>{% endfor %}</tbody></table>
{% else %}<p>{% if superseded_pull_requests %}No file changes. Older migration pull requests will be closed and their unused branches deleted.{% elif existing_pull_request_url %}No file changes. The pull request's recorded engine commit will be refreshed.{% else %}No migration file changes are needed.{% endif %}</p>{% endif %}
{% if not up_to_date %}<form action="/setup/confirm" method="post"><p><input type="hidden" name="state" value="{{ state_token }}"><button type="submit">{% if existing_pull_request_url %}Update migration pull request{% else %}Create migration pull request{% endif %}</button></p></form>{% endif %}
</main></body></html>
