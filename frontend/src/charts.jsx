import React from 'react';
import { createRoot } from 'react-dom/client';
import { BarChart } from '@mui/x-charts/BarChart';
import { PieChart } from '@mui/x-charts/PieChart';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import Box from '@mui/material/Box';
import Tooltip from '@mui/material/Tooltip';
import { animated, to } from '@react-spring/web';

// Dark-mode theme tuned for the Aroya Repeat Guests dashboard
const muiTheme = createTheme({
  palette: {
    mode: 'dark',
    background: { default: '#0f172a', paper: '#1e293b' },
    text: { primary: '#f8fafc', secondary: '#cbd5e1' },
    divider: 'rgba(148,163,184,0.12)',
  },
  typography: { fontFamily: "'Inter', 'Roboto', 'Segoe UI', sans-serif" },
});

const roots = new Map();

function formatMonthShort(yyyymm) {
  const [y, m] = yyyymm.split('-');
  return new Date(+y, +m - 1).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' });
}
function formatMoney(v) {
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return Math.round(v / 1e3) + 'K';
  return Math.round(v).toString();
}
function lerpColor(start, end, t) {
  const s = start.match(/\w\w/g).map((x) => parseInt(x, 16));
  const e = end.match(/\w\w/g).map((x) => parseInt(x, 16));
  const r = Math.round(s[0] + (e[0] - s[0]) * t);
  const g = Math.round(s[1] + (e[1] - s[1]) * t);
  const b = Math.round(s[2] + (e[2] - s[2]) * t);
  return `rgb(${r},${g},${b})`;
}

function Heatmap({ rows, cols, data }) {
  if (!rows.length || !cols.length) {
    return <Box sx={{ p: 5, textAlign: 'center', color: '#64748b' }}>Not enough data for heatmap.</Box>;
  }
  let maxVal = 0;
  rows.forEach((r) => cols.forEach((c) => {
    const v = (data[r] && data[r][c]) || 0;
    if (v > maxVal) maxVal = v;
  }));
  const COLD = '1a2538';
  const HOT = '4DD0E1';
  return (
    <Box sx={{ p: 2, overflowX: 'auto' }}>
      <Box component="table" sx={{
        borderCollapse: 'separate',
        borderSpacing: '3px',
        fontFamily: "'Inter','Roboto',sans-serif",
        width: '100%',
      }}>
        <thead>
          <tr>
            <th style={{ width: 140 }}></th>
            {cols.map((c) => (
              <th key={c} style={{
                fontSize: 10, color: '#9CA3AF', fontWeight: 500, padding: '6px 4px',
                textAlign: 'center', whiteSpace: 'nowrap',
              }}>{formatMonthShort(c)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r}>
              <td style={{
                fontSize: 12, color: '#cbd5e1', fontWeight: 500,
                padding: '6px 10px', textAlign: 'right', whiteSpace: 'nowrap',
              }}>{r}</td>
              {cols.map((c) => {
                const v = (data[r] && data[r][c]) || 0;
                if (v === 0) {
                  return (
                    <td key={c} style={{
                      background: 'rgba(148,163,184,0.04)', borderRadius: 4,
                      padding: '8px 6px', textAlign: 'center',
                      color: '#475569', fontSize: 11,
                    }}>·</td>
                  );
                }
                const pct = maxVal > 0 ? v / maxVal : 0;
                const bg = lerpColor(COLD, HOT, pct);
                const fg = pct > 0.55 ? '#0f172a' : '#e2e8f0';
                return (
                  <Tooltip
                    key={c}
                    title={`${r} · ${formatMonthShort(c)}: ${Math.round(v).toLocaleString()}`}
                    arrow
                    placement="top"
                    componentsProps={{
                      tooltip: { sx: { fontFamily: "'Inter','Roboto',sans-serif", fontSize: 12 } },
                    }}
                  >
                    <td style={{
                      background: bg, color: fg, borderRadius: 4,
                      padding: '8px 6px', textAlign: 'center',
                      fontSize: 11, fontWeight: 600, cursor: 'default',
                      transition: 'transform 0.1s',
                    }}>
                      {formatMoney(v)}
                    </td>
                  </Tooltip>
                );
              })}
            </tr>
          ))}
        </tbody>
      </Box>
      <Box sx={{
        mt: 1.5, display: 'flex', alignItems: 'center', gap: 1.5,
        fontSize: 11, color: '#94a3b8', fontFamily: "'Inter','Roboto',sans-serif",
      }}>
        <span>Low</span>
        <Box sx={{
          width: 120, height: 8, borderRadius: 4,
          background: `linear-gradient(to right, #${COLD}, #${HOT})`,
        }} />
        <span>High</span>
        <span style={{ marginLeft: 'auto' }}>Peak: {Math.round(maxVal).toLocaleString()}</span>
      </Box>
    </Box>
  );
}

function mountHeatmap(id, hm) {
  const el = document.getElementById(id);
  if (!el) { console.warn('[mui-charts] heatmap target missing:', id); return; }
  let root = roots.get(id);
  if (!root) {
    root = createRoot(el);
    roots.set(id, root);
  }
  root.render(
    <ThemeProvider theme={muiTheme}>
      <Heatmap rows={hm.cabins.slice(0, 10)} cols={hm.months} data={hm.data} />
    </ThemeProvider>
  );
}

const LABEL_STYLE = {
  fill: '#e2e8f0',
  fontSize: 11,
  fontWeight: 600,
  fontFamily: "'Inter','Roboto','Segoe UI',sans-serif",
  paintOrder: 'stroke',
  stroke: 'rgba(15,23,42,0.85)',
  strokeWidth: 3,
  strokeLinejoin: 'round',
};

// MUI X passes an animated `style` from react-spring containing x, y, width, height
// (where x, y are bar-CENTER coords). We override the y/x using `to()` to push the
// label OUTSIDE the bar instead of inside.
function BarLabelAbove({ style, children, ownerState, ...rest }) {
  const newY = style && style.y && style.height
    ? to([style.y, style.height], (y, h) => y - h / 2 - 6)
    : style?.y;
  return (
    <animated.text
      {...rest}
      x={style?.x}
      y={newY}
      textAnchor="middle"
      dominantBaseline="auto"
      style={LABEL_STYLE}
    >
      {children}
    </animated.text>
  );
}

function BarLabelRight({ style, children, ownerState, ...rest }) {
  const newX = style && style.x && style.width
    ? to([style.x, style.width], (x, w) => x + w / 2 + 6)
    : style?.x;
  return (
    <animated.text
      {...rest}
      x={newX}
      y={style?.y}
      textAnchor="start"
      dominantBaseline="central"
      style={LABEL_STYLE}
    >
      {children}
    </animated.text>
  );
}

function mountChart(id, type, props) {
  const el = document.getElementById(id);
  if (!el) {
    console.warn('[mui-charts] target missing:', id);
    return;
  }
  let root = roots.get(id);
  if (!root) {
    root = createRoot(el);
    roots.set(id, root);
  }
  let finalProps = props;
  if (type === 'bar') {
    const horizontal = props.layout === 'horizontal';
    finalProps = {
      ...props,
      slots: {
        ...(props.slots || {}),
        barLabel: horizontal ? BarLabelRight : BarLabelAbove,
      },
    };
  }
  const ChartComp = type === 'pie' ? PieChart : BarChart;
  try {
    root.render(
      <ThemeProvider theme={muiTheme}>
        <ChartComp {...finalProps} />
      </ThemeProvider>
    );
  } catch (err) {
    console.error('[mui-charts] render failed for', id, err, props);
  }
}

function unmountChart(id) {
  const root = roots.get(id);
  if (root) {
    root.unmount();
    roots.delete(id);
  }
}

// Expose the global API
window._mountMuiChart = mountChart;
window._unmountMuiChart = unmountChart;
window._mountMuiHeatmap = mountHeatmap;

// Flush any mounts queued before the bundle loaded
if (Array.isArray(window._muiChartQueue)) {
  const q = window._muiChartQueue;
  window._muiChartQueue = [];
  q.forEach((args) => {
    try { mountChart(...args); }
    catch (e) { console.error('[mui-charts] mount failed', args[0], e); }
  });
}
if (Array.isArray(window._muiHeatmapQueue)) {
  const q = window._muiHeatmapQueue;
  window._muiHeatmapQueue = [];
  q.forEach((args) => {
    try { mountHeatmap(...args); }
    catch (e) { console.error('[mui-charts] heatmap mount failed', args[0], e); }
  });
}

window.dispatchEvent(new CustomEvent('mui-charts-ready'));
console.log('[mui-charts] bundle initialised');
