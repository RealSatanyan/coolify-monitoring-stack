#!/usr/bin/env python3
"""Generate Grafana dashboards at image build time.

Writes two dashboards into the directory given as argv[1]:
  - node-exporter-overview.json : compact custom server overview (incl. swap)
  - node-exporter-full.json     : Grafana.com dashboard 1860 "Node Exporter Full",
                                  datasource pinned to our provisioned Prometheus.

Committing the 468 KB 1860 JSON to git is avoided by fetching it here. The fetch is
best-effort: if grafana.com is unreachable the build still succeeds with the overview
dashboard only (so a network blip never takes Grafana down).
"""
import json
import os
import sys
import urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else "/etc/grafana/dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
SEL = '{job="node",instance=~"$instance"}'


def tgt(rid, expr, leg):
    return {"datasource": DS, "editorMode": "code", "expr": expr,
            "legendFormat": leg, "range": True, "refId": rid}


def stat(pid, title, x, y, expr, steps, unit="percent", color_mode="background",
         graph="area", fixed=None):
    defaults = {"unit": unit}
    if unit == "percent":
        defaults.update({"min": 0, "max": 100})
    if fixed:
        defaults["color"] = {"mode": "fixed", "fixedColor": fixed}
    else:
        defaults["color"] = {"mode": "thresholds"}
        defaults["thresholds"] = {"mode": "absolute", "steps": steps}
    return {"id": pid, "type": "stat", "title": title, "datasource": DS,
            "gridPos": {"h": 4, "w": 6, "x": x, "y": y},
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "orientation": "auto", "textMode": "auto", "colorMode": color_mode,
                        "graphMode": graph, "justifyMode": "auto"},
            "targets": [tgt("A", expr, title)]}


def ts(pid, title, x, y, unit, targets, extra=None, fill=10):
    fc = {"unit": unit, "color": {"mode": "palette-classic"},
          "custom": {"drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2,
                     "fillOpacity": fill, "showPoints": "never", "spanNulls": False,
                     "axisPlacement": "auto"}}
    if extra:
        fc.update(extra)
    return {"id": pid, "type": "timeseries", "title": title, "datasource": DS,
            "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
            "fieldConfig": {"defaults": fc, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "calcs": []},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "targets": targets}


def overview():
    g_steps = [{"color": "green", "value": None}, {"color": "yellow", "value": 70}, {"color": "red", "value": 85}]
    m_steps = [{"color": "green", "value": None}, {"color": "yellow", "value": 75}, {"color": "red", "value": 90}]
    cpu_idle = f'avg by (instance) (rate(node_cpu_seconds_total{{job="node",mode="idle",instance=~"$instance"}}[5m]))'
    fs = 'fstype!~"tmpfs|overlay|squashfs|nsfs|ramfs"'
    panels = [
        stat(1, "CPU Usage", 0, 0, f'100 - (avg(rate(node_cpu_seconds_total{{job="node",mode="idle",instance=~"$instance"}}[5m])) * 100)', g_steps),
        stat(2, "Memory Usage", 6, 0, f'100 * (1 - (node_memory_MemAvailable_bytes{SEL} / node_memory_MemTotal_bytes{SEL}))', m_steps),
        stat(3, "Disk Usage", 12, 0, f'100 - (sum(node_filesystem_avail_bytes{{job="node",instance=~"$instance",{fs}}}) / sum(node_filesystem_size_bytes{{job="node",instance=~"$instance",{fs}}}) * 100)', m_steps),
        stat(4, "Uptime", 18, 0, f'time() - node_boot_time_seconds{SEL}', None, unit="s", color_mode="value", graph="none", fixed="blue"),
        ts(5, "CPU Usage", 0, 4, "percent", [tgt("A", f'100 - ({cpu_idle} * 100)', "{{instance}}")], {"min": 0, "max": 100}),
        ts(6, "Memory", 12, 4, "bytes", [
            tgt("A", f'node_memory_MemTotal_bytes{SEL}', "Total"),
            tgt("B", f'node_memory_MemTotal_bytes{SEL} - node_memory_MemAvailable_bytes{SEL}', "Used")], {"min": 0}),
        ts(7, "Load Average", 0, 12, "short", [
            tgt("A", f'node_load1{SEL}', "1m"), tgt("B", f'node_load5{SEL}', "5m"),
            tgt("C", f'node_load15{SEL}', "15m")], {"min": 0}, fill=5),
        ts(8, "Network Traffic", 12, 12, "Bps", [
            tgt("A", f'sum by (instance) (rate(node_network_receive_bytes_total{{job="node",instance=~"$instance",device!~"lo|veth.*|docker.*|br-.*|cali.*|cni.*|flannel.*"}}[5m]))', "RX {{instance}}"),
            tgt("B", f'sum by (instance) (rate(node_network_transmit_bytes_total{{job="node",instance=~"$instance",device!~"lo|veth.*|docker.*|br-.*|cali.*|cni.*|flannel.*"}}[5m]))', "TX {{instance}}")]),
        ts(9, "Disk I/O", 0, 20, "Bps", [
            tgt("A", f'sum by (instance) (rate(node_disk_read_bytes_total{SEL}[5m]))', "Read {{instance}}"),
            tgt("B", f'sum by (instance) (rate(node_disk_written_bytes_total{SEL}[5m]))', "Write {{instance}}")]),
        ts(10, "Filesystem Usage by Mount", 12, 20, "percent", [
            tgt("A", f'100 - (node_filesystem_avail_bytes{{job="node",instance=~"$instance",{fs}}} / node_filesystem_size_bytes{{job="node",instance=~"$instance",{fs}}} * 100)', "{{mountpoint}}")], {"min": 0, "max": 100}, fill=5),
        ts(11, "Swap", 0, 28, "bytes", [
            tgt("A", f'node_memory_SwapTotal_bytes{SEL}', "Total"),
            tgt("B", f'node_memory_SwapTotal_bytes{SEL} - node_memory_SwapFree_bytes{SEL}', "Used")], {"min": 0}),
        ts(12, "Swap Usage", 12, 28, "percent", [
            tgt("A", f'100 * (1 - node_memory_SwapFree_bytes{SEL} / node_memory_SwapTotal_bytes{SEL})', "Swap %")], {"min": 0, "max": 100}),
    ]
    return {
        "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                  "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
                                  "name": "Annotations & Alerts", "type": "dashboard"}]},
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 1, "id": None, "links": [],
        "liveNow": False, "panels": panels, "refresh": "30s", "schemaVersion": 39,
        "tags": ["node-exporter", "server"],
        "templating": {"list": [{
            "current": {}, "datasource": DS,
            "definition": 'label_values(node_uname_info{job="node"}, instance)',
            "hide": 0, "includeAll": True, "label": "Instance", "multi": True, "name": "instance",
            "options": [], "query": {"query": 'label_values(node_uname_info{job="node"}, instance)',
                                     "refId": "StandardVariableQuery"},
            "refresh": 2, "regex": "", "sort": 1, "type": "query"}]},
        "time": {"from": "now-6h", "to": "now"}, "timepicker": {}, "timezone": "",
        "title": "Server Overview (Node Exporter)", "uid": "node-exporter-overview",
        "version": 1, "weekStart": "",
    }


def node_exporter_full():
    url = "https://grafana.com/api/dashboards/1860/revisions/latest/download"
    req = urllib.request.Request(url, headers={"User-Agent": "coolify-monitoring-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    for v in d.get("templating", {}).get("list", []):
        if v.get("type") == "datasource":
            v["current"] = {"selected": True, "text": "Prometheus", "value": "prometheus"}
    d["id"] = None
    return d


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "node-exporter-overview.json"), "w") as f:
        json.dump(overview(), f, indent=2)
    print("wrote node-exporter-overview.json (12 panels incl. swap)")
    try:
        nef = node_exporter_full()
        with open(os.path.join(OUT, "node-exporter-full.json"), "w") as f:
            json.dump(nef, f, indent=2)
        print(f"wrote node-exporter-full.json ({len(nef.get('panels', []))} panels)")
    except Exception as e:  # best-effort: never fail the build over this
        print(f"WARNING: could not fetch Node Exporter Full (1860): {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
