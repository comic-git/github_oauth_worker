<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Connect CMS repository</title></head>
<body>
<main><h1>Connect CMS repository</h1><p>{{ origin }}</p>
<form action="/enroll/select" method="post">
<input type="hidden" name="state" value="{{ state_token }}">
{% for installation_id, repository in choices %}
<label><input type="radio" name="selection" value="{{ installation_id }}:{{ repository.id }}" required> {{ repository.owner.login }}/{{ repository.name }}</label><br>
{% endfor %}
<button type="submit">Connect repository</button>
</form></main>
</body></html>
