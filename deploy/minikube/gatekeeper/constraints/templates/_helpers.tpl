{{/* match: every Pod outside the shared and the constraint's own excluded namespaces. */}}
{{- define "constraints.match" -}}
match:
  kinds:
    - apiGroups: [""]
      kinds: [Pod]
  excludedNamespaces: {{ concat .root.Values.excludedNamespaces .c.excludedNamespaces | uniq | toJson }}
{{- end -}}

{{/* metadata and spec header shared by every constraint. */}}
{{- define "constraints.header" -}}
apiVersion: constraints.gatekeeper.sh/v1beta1
kind: {{ .kind }}
metadata:
  name: {{ .name }}
  labels:
    app.kubernetes.io/part-of: minikube-infrastructure
    app.kubernetes.io/managed-by: {{ .root.Release.Service }}
spec:
  {{- if not (has .c.enforcementAction (list "deny" "warn" "dryrun")) }}
  {{- fail (printf "%s.enforcementAction must be deny, warn or dryrun" .key) }}
  {{- end }}
  enforcementAction: {{ .c.enforcementAction }}
  {{- include "constraints.match" . | nindent 2 }}
{{- end -}}
