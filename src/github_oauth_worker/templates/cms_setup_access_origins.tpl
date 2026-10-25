<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Approve CMS websites</title></head>
<body><main><h1>Approve CMS websites</h1>
<p>{{ repository.owner.login }}/{{ repository.name }} · CMS branch: {{ target_branch }}</p>
<p>These website origins will be allowed to receive a CMS token for this repository. A path such as <code>/comic/</code> is not part of an origin; GitHub Pages projects under the same account share one origin.</p>
<form action="/setup/review-access" method="post">
<input type="hidden" name="state" value="{{ state_token }}">
<label for="approved_origins">HTTPS origins, one per line</label>
<textarea id="approved_origins" name="approved_origins" rows="5" cols="60" required>{{ current_origins|join('\n') }}</textarea>
<p>Worker: <code>{{ worker_origin }}</code></p>
<p>Access approval will be proposed on the repository's default branch, <code>{{ default_branch }}</code>.</p>
<p><button type="submit">Review CMS setup</button></p>
</form>
</main></body></html>
