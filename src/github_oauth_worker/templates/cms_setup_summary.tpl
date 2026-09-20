<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Review CMS migration</title></head>
<body><main><h1>Review CMS migration</h1>
<p>{{ repository.owner.login }}/{{ repository.name }}</p>
<p>Target branch: {{ preview.target_branch }}</p>
<p>Engine selector: {{ preview.engine_selector }}</p>
<p>Resolved engine commit: {{ preview.engine_commit_sha }}</p>
<h2>Files to add</h2><ul>{% for path in preview.changed_paths %}<li>{{ path }}</li>{% endfor %}</ul>
<form action="/setup/confirm" method="post"><input type="hidden" name="state" value="{{ state_token }}"><button type="submit">Create migration pull request</button></form>
</main></body></html>
