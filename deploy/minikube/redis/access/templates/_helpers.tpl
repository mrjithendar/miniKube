{{- define "redis.selector" -}}
app.kubernetes.io/name: valkey
app.kubernetes.io/instance: {{ .Values.release }}
{{- end -}}

{{- define "redis.requireAllowlist" -}}
{{- if empty .Values.allowedIps }}
{{- fail "allowedIps is empty: set var.redis_allowed_ips (vars/mk4.tfvars) to the client addresses allowed in" }}
{{- end }}
{{- end -}}
