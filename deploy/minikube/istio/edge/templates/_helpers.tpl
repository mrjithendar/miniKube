{{/* Delegate of the routing table for an add-on whose upstream chart renders only an Ingress. Serves under its own
     prefix, so nothing is rewritten. */}}
{{- define "edge.addonRoute" -}}
{{- $r := index .root.Values.addonRoutes .name -}}
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata:
  name: {{ .name | quote }}
  namespace: {{ $r.namespace | quote }}
spec:
  http:
    - name: {{ .name | quote }}
      route:
        - destination:
            host: {{ printf "%s.%s.svc.cluster.local" $r.host $r.namespace | quote }}
            port: {number: {{ $r.port }}}
{{- end }}

{{/* connection.sni values of the TLS (TCP) hosts: HTTP-only DENY rules must never apply to them. */}}
{{- define "edge.tlsSnis" -}}
{{- $out := list }}{{ range .Values.gateway.tls }}{{ $out = append $out (last (splitList "/" .host)) }}{{ end }}{{ toJson $out }}
{{- end }}
