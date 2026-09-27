{{- define "sentinel.labels" -}}
app.kubernetes.io/name: sentinel
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: sentinel-{{ .Chart.Version }}
{{- end -}}
{{- define "sentinel.image" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{- $registry := required "imageRegistry must name your verified GHCR namespace" $root.Values.imageRegistry -}}
{{- $digest := index $root.Values.imageDigests $component -}}
{{- if $digest -}}
{{- printf "%s/sentinel-%s@%s" $registry $component $digest -}}
{{- else -}}
{{- if eq $root.Values.environment "production" -}}
{{- fail "Production images require immutable imageDigests for every component" -}}
{{- end -}}
{{- printf "%s/sentinel-%s:%s" $registry $component (required "imageTag is required for demo builds" $root.Values.imageTag) -}}
{{- end -}}
{{- end -}}
