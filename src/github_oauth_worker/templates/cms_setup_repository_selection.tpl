<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Select comic repository</title></head>
<body><main><h1>Select comic repository</h1>
<form action="/setup/select" method="post">
<input type="hidden" name="state" value="{{ state_token }}">
{% for repository in repositories %}
<label><input type="radio" name="repository_id" value="{{ repository.id }}" required> {{ repository.owner.login }}/{{ repository.name }}</label><br>
{% endfor %}
<p><button type="submit">Review CMS migration</button></p>
</form></main></body></html>
