{{/* ALLOW AuthorizationPolicy for one namespace (values.yaml, authorization): only its own workloads and the listed
     namespaces' (their mTLS identity) may call anything there. One template per namespace, one resource each. */}}
{{- define "mesh.allowKnownCallers" -}}
{{- $ns := .namespace -}}
{{- if eq $ns "istio-system" }}{{ fail "authorization.namespaces must not list istio-system: a policy there applies to the whole mesh" }}{{ end -}}
{{- $from := index .root.Values.authorization.namespaces $ns -}}
{{- if $from -}}
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: allow-known-callers
  namespace: {{ $ns | quote }}
  {{- if .root.Values.authorization.dryRun }}
  annotations:
    istio.io/dry-run: "true"
  {{- end }}
spec:
  action: ALLOW
  rules:
    - from:
        - source:
            namespaces: {{ toJson (prepend $from $ns) }}
{{- end }}
{{- end }}
