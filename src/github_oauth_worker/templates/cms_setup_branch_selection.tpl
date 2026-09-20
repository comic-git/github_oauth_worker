<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Select comic branch</title></head>
<body><main><h1>Select comic branch</h1>
<p>{{ repository.owner.login }}/{{ repository.name }}</p>
<form action="/setup/select-branch" method="post">
<input type="hidden" name="state" value="{{ state_token }}">
<label for="target_branch">Branch</label>
<select id="target_branch" name="target_branch" required>
{% for branch in branches %}<option value="{{ branch.name }}"{% if branch.name == repository.default_branch %} selected{% endif %}>{{ branch.name }}</option>{% endfor %}
</select>
<button type="submit">Review CMS migration</button>
</form></main></body></html>
