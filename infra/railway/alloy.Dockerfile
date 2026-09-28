FROM grafana/alloy:v1.20.0@sha256:f111cce835516c5f99166342be7038496b52ced16667be5a11e19258a3e4cd30
COPY infra/railway/config.alloy /etc/alloy/config.alloy
COPY --chmod=755 infra/railway/start-alloy.sh /usr/local/bin/start-sentinel-alloy
RUN mkdir -p /var/lib/alloy/data && chown -R 473:473 /var/lib/alloy
USER 473
EXPOSE 12345
ENTRYPOINT ["/usr/local/bin/start-sentinel-alloy"]
