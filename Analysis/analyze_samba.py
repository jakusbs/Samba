"""
analyze_samba.py — SOT/MOKE analysis class for SAMBA HDF5 and Salsa NXS data.

Catppuccin Mocha line colors:
  DL  : #a6e3a1 (green)
  Oe  : #89b4fa (blue)
  refl: #f38ba8 (red)
"""

import os
import warnings
import datetime
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.interpolate import interp1d

import analysis_field
from samba_io import (
    load_samba_h5,
    load_samba_scanlist,
    load_salsa_nxs,
    load_salsa_scanlist,
    group_by_sign,
    average_scans,
)

# ---------------------------------------------------------------------------
# Catppuccin Mocha palette
# ---------------------------------------------------------------------------
_C_DL    = '#a6e3a1'   # green
_C_OE    = '#89b4fa'   # blue
_C_REFL  = '#f38ba8'   # red
_C_DL_FIT = '#40b08e'  # darker green for fit line
_C_OE_FIT = '#4a9fd4'  # darker blue for fit line


class SambaSOTAnalysis:
    """SOT analysis for SAMBA HDF5 or Salsa NXS scanlists."""

    def __init__(
        self,
        scanlist_path: str,
        x1_ch: str,
        y1_ch: str,
        reflec_ch: str,
        x2_ch: str = None,
        y2_ch: str = None,
        phase: float = 0.0,
        calibration: float = 1.0,
        current_mA: float = 10.0,
        resistance_ratio: float = 1.0,
        setup: str = 'samba',
        direction: str = None,
        ignore_lines: list = None,
        data_base_dir: str = None,
        x_scale: float = 1e-3,
        x_unit: str = 'µm',
        signal_unit: str = 'µV',
    ):
        """
        Parameters
        ----------
        scanlist_path : path to scanlist .txt file
        x1_ch, y1_ch : 1st-harmonic X and Y channel keys in the loaded scan dict
        reflec_ch    : reflection channel key
        x2_ch, y2_ch : optional 2nd-harmonic channels
        phase        : lock-in phase offset in degrees
        calibration  : Kerr conversion (signal units → µrad); set signal_unit='µrad' too
        current_mA   : applied current in mA
        resistance_ratio : R_NM/M / R_M for current correction (Ic_eff = Ic * ratio)
        setup        : 'samba' or 'salsa'
        direction    : 'trace', 'retrace', or None (all)
        ignore_lines : 1-based line indices to skip (applied before grouping)
        data_base_dir: alternate base directory for resolving scan paths
        signal_unit  : unit label for Kerr-signal y-axes (default 'µV'; use 'µrad' when
                       calibration converts to µrad)
        """
        self.scanlist_path = str(scanlist_path)
        self.x1_ch = x1_ch
        self.y1_ch = y1_ch
        self.reflec_ch = reflec_ch
        self.x2_ch = x2_ch
        self.y2_ch = y2_ch
        self.phase = float(phase)
        self.calibration = float(calibration)
        self.current_mA = float(current_mA)
        self.resistance_ratio = float(resistance_ratio)
        self.setup = setup.lower()
        self.direction = direction
        self.ignore_lines = set(ignore_lines) if ignore_lines else set()
        self.data_base_dir = data_base_dir
        self.x_scale = float(x_scale)    # multiply x_ref by this before plotting (nm→µm)
        self.x_unit  = str(x_unit)
        self.signal_unit = str(signal_unit)

        # Outputs populated by load_data / evaluate_data
        self.entries_pos = []
        self.entries_neg = []
        self.scans_pos = []
        self.scans_neg = []
        self.avg_pos = {}
        self.avg_neg = {}
        self.edges = None
        self.dev_center = None
        self.width = None
        self.x_ref = None
        self.theta_DL = None
        self.theta_Oe = None
        self.theta_DL_err = None
        self.theta_Oe_err = None
        self.reflec_avg = None
        self.scans_all = []
        self.entries_all = []

        # Fit results
        self.fit_DL_mT = None
        self.fit_DL_error_mT = None
        self.fit_Oe_A = None
        self.fit_Oe_A0 = None

        # Output directory for plots (created lazily)
        self._plot_dir = None

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def from_samba(cls, scanlist_path: str, x1_ch: str, y1_ch: str,
                   reflec_ch: str, **kwargs):
        """Construct from a SAMBA scanlist."""
        return cls(scanlist_path, x1_ch, y1_ch, reflec_ch,
                   setup='samba', **kwargs)

    @classmethod
    def from_salsa(cls, scanlist_path: str, x1_ch: str, y1_ch: str,
                   reflec_ch: str, **kwargs):
        """Construct from a Salsa scanlist."""
        return cls(scanlist_path, x1_ch, y1_ch, reflec_ch,
                   setup='salsa', **kwargs)

    @classmethod
    def import_analyze(
        cls,
        scanlist_path: str,
        x1_ch: str,
        y1_ch: str,
        reflec_ch: str,
        see_channels: list = None,
        ignore_lines: list = None,
        fit_edge_offset: int = 5,
        **kwargs,
    ) -> 'SambaSOTAnalysis':
        """Load data and run the full analysis pipeline.

        Equivalent to analyze_SHE_OHE.import_analyze_SOT:
          1. load_data()
          2. see_intensity() for each channel in see_channels
          3. evaluate_data('sumdiff')
          4. evaluate_data('negpos')
          5. evaluate_data('realimag')
          6. eval_width_and_fit()

        Parameters
        ----------
        scanlist_path : path to scanlist .txt file
        x1_ch, y1_ch : lock-in 1st-harmonic X and Y channel names
        reflec_ch     : reflection/intensity channel name (None to skip edge fit)
        see_channels  : list of channel names for see_intensity plots (default: [x1_ch])
        ignore_lines  : 0-based line indices to skip
        fit_edge_offset : points to exclude at each device edge during fitting
        **kwargs      : passed to constructor (phase, calibration, current_mA,
                        direction, data_base_dir, x_scale, x_unit, ...)
        """
        res = cls(scanlist_path, x1_ch, y1_ch, reflec_ch,
                  ignore_lines=ignore_lines, **kwargs)
        res.load_data()

        for ch in (see_channels or [x1_ch]):
            res.see_intensity(ch)

        res.evaluate_data(do_plot='sumdiff')
        res.evaluate_data(do_plot='negpos')
        res.evaluate_data(do_plot='realimag')

        if reflec_ch:
            res.get_edges()
            res.eval_width_and_fit(co=fit_edge_offset)

        return res

    @classmethod
    def import_analyze_both(
        cls,
        scanlist_path: str,
        x1_ch: str,
        y1_ch: str,
        reflec_ch: str,
        see_channels: list = None,
        ignore_lines: list = None,
        fit_edge_offset: int = 5,
        **kwargs,
    ) -> tuple['SambaSOTAnalysis', 'SambaSOTAnalysis']:
        """Run import_analyze separately for trace and retrace scans.

        The scanlist is expected to contain both _trace and _retrace files
        (as produced by the Cryo piezo scanner).  Trace and retrace are
        analysed independently because piezo hysteresis shifts the real
        sample position between the two directions.

        Returns (res_trace, res_retrace).
        """
        print("=" * 60)
        print("TRACE")
        print("=" * 60)
        res_trace = cls.import_analyze(
            scanlist_path, x1_ch, y1_ch, reflec_ch,
            see_channels=see_channels,
            ignore_lines=ignore_lines,
            fit_edge_offset=fit_edge_offset,
            direction='trace',
            **kwargs,
        )

        print("=" * 60)
        print("RETRACE")
        print("=" * 60)
        res_retrace = cls.import_analyze(
            scanlist_path, x1_ch, y1_ch, reflec_ch,
            see_channels=see_channels,
            ignore_lines=ignore_lines,
            fit_edge_offset=fit_edge_offset,
            direction='retrace',
            **kwargs,
        )

        return res_trace, res_retrace

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_plot_dir(self) -> str:
        if self._plot_dir is None:
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            base = os.path.dirname(os.path.abspath(self.scanlist_path))
            suffix = f'_{self.direction}' if self.direction else ''
            self._plot_dir = os.path.join(base, ts + suffix)
            os.makedirs(self._plot_dir, exist_ok=True)
        return self._plot_dir

    def _base_title(self) -> str:
        name = os.path.splitext(os.path.basename(self.scanlist_path))[0]
        return f'{name}  [{self.direction}]' if self.direction else name

    def _load_samba_entry(self, entry: dict) -> dict | None:
        """Load one SAMBA entry; return scan dict or None on failure."""
        path = entry['path']
        if not os.path.exists(path):
            warnings.warn(f"  File not found, skipping: {path}")
            return None
        scan = load_samba_h5(path)
        if 'error' in scan:
            warnings.warn(f"  Error loading {path}: {scan['error']}")
            return None
        scan['field_T'] = entry['field_T']
        return scan

    def _load_salsa_entry(self, entry: dict) -> dict | None:
        """Load one Salsa entry; return merged scan dict or None on failure."""
        path = entry['path']
        if not os.path.exists(path):
            warnings.warn(f"  File not found, skipping: {path}")
            return None
        result = load_salsa_nxs(path)

        # Select trace or retrace or merge both
        if self.direction == 'trace':
            scan = result['trace']
        elif self.direction == 'retrace':
            scan = result['retrace']
        else:
            # Default: use trace (first half)
            scan = result['trace']

        scan = dict(scan)
        scan['field_T'] = entry['field_T']
        return scan

    def _channels_for_averaging(self) -> list:
        chs = [self.x1_ch, self.y1_ch, self.reflec_ch]
        if self.x2_ch:
            chs.append(self.x2_ch)
        if self.y2_ch:
            chs.append(self.y2_ch)
        return chs

    def _safe_get(self, scan: dict, ch: str) -> np.ndarray | None:
        """Get channel from scan dict, warn on KeyError."""
        if ch not in scan:
            warnings.warn(f"Channel '{ch}' not found in scan {scan.get('path', '?')}")
            return None
        return np.asarray(scan[ch], dtype=float)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load_data(self) -> 'SambaSOTAnalysis':
        """Load all scans from scanlist and compute averaged pos/neg groups."""
        print(f"[SambaSOTAnalysis] Loading {self.setup} scanlist: "
              f"{os.path.basename(self.scanlist_path)}")

        # Parse scanlist
        if self.setup == 'samba':
            entries = load_samba_scanlist(
                self.scanlist_path,
                direction=self.direction,
                data_base_dir=self.data_base_dir,
            )
        else:
            entries = load_salsa_scanlist(
                self.scanlist_path,
                base_dir=self.data_base_dir,
            )

        if not entries:
            warnings.warn("load_data: no entries found in scanlist")
            return self

        # Apply ignore_lines (1-based)
        if self.ignore_lines:
            entries = [e for i, e in enumerate(entries, start=1)
                       if i not in self.ignore_lines]
            print(f"  {len(self.ignore_lines)} line(s) ignored")

        print(f"  {len(entries)} entries to load")

        # Load files
        scans = []
        for i, entry in enumerate(entries, start=1):
            if self.setup == 'samba':
                scan = self._load_samba_entry(entry)
            else:
                scan = self._load_salsa_entry(entry)
            if scan is not None:
                scans.append((entry, scan))

        print(f"  {len(scans)} files loaded successfully")

        if not scans:
            warnings.warn("load_data: no files could be loaded")
            return self

        # Group by effective field sign
        loaded_entries = [e for e, _ in scans]
        loaded_scans   = [s for _, s in scans]

        self.scans_all  = loaded_scans
        self.entries_all = loaded_entries

        pos_scans, neg_scans = group_by_sign(loaded_entries, loaded_scans)

        # Also keep matching entries
        pos_entries = [e for e, s in scans
                       if s in pos_scans]
        neg_entries = [e for e, s in scans
                       if s in neg_scans]

        self.entries_pos = pos_entries
        self.entries_neg = neg_entries
        self.scans_pos   = pos_scans
        self.scans_neg   = neg_scans

        print(f"  Positive-field group: {len(pos_scans)} scans")
        print(f"  Negative-field group: {len(neg_scans)} scans")
        self.print_channels()

        self.get_avg_data()
        return self

    def print_channels(self) -> 'SambaSOTAnalysis':
        """Print all data channels found in the first loaded scan."""
        if not self.scans_all:
            print("  No scans loaded yet.")
            return self
        first = self.scans_all[0]
        keys = [k for k, v in first.items()
                if isinstance(v, np.ndarray) and k != 'x']
        print(f"  Available channels: {keys}")
        return self

    def get_avg_data(self) -> 'SambaSOTAnalysis':
        """Average pos and neg scan groups onto a common x grid."""
        channels = self._channels_for_averaging()
        all_scans = self.scans_pos + self.scans_neg

        if not all_scans:
            return self

        # Build common x reference from first available scan (raw hardware units)
        first = all_scans[0]
        x_ref_raw = np.array(first['x'], dtype=float)
        order = np.argsort(x_ref_raw)
        x_ref_raw = x_ref_raw[order]

        # Interpolate in raw units so scan['x'] and x_ref share the same scale
        self.avg_pos = average_scans(self.scans_pos, channels, x_ref_raw)
        self.avg_neg = average_scans(self.scans_neg, channels, x_ref_raw)

        # Scale x_ref for display only, after averaging is done
        self.x_ref = x_ref_raw * self.x_scale
        return self

    def see_intensity(self, ch: str) -> 'SambaSOTAnalysis':
        """Plot per-scan mean intensity and all profiles (copper colormap)."""
        scans = self.scans_all
        if not scans:
            warnings.warn("see_intensity: no scans loaded")
            return self

        profiles  = [s[ch] for s in scans if ch in s]
        positions = [s['x'] * self.x_scale for s in scans if ch in s]
        means     = [float(np.mean(p)) for p in profiles]

        if not profiles:
            warnings.warn(f"see_intensity: channel '{ch}' not found in any scan")
            return self

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 12),
                                       gridspec_kw={'height_ratios': [1, 3]})
        fig.suptitle(f"{self._base_title()}\n{ch}", fontsize=10)

        ax1.plot(means, '.-')
        ax1.set_xticks(range(len(means)))
        ax1.set_xlabel('scan number')
        ax1.set_ylabel('mean [µV]')
        ax1.grid(True)

        colors = plt.cm.copper(np.linspace(0, 1, len(profiles)))
        for i, (xi, yi) in enumerate(zip(positions, profiles)):
            ax2.plot(xi, yi, 'x-', color=colors[i], label=str(i))
        ax2.axhline(0, color='r', linestyle='-')
        ax2.set_xlabel(f'x [{self.x_unit}]')
        ch_unit = self.scans_all[0]['units'].get(ch, '') if self.scans_all else ''
        ax2.set_ylabel(f'{ch} [{ch_unit}]' if ch_unit else ch)
        ax2.grid(True)
        if len(profiles) <= 12:
            ax2.legend(fontsize=8)

        plt.tight_layout()
        pdir = self._get_plot_dir()
        fname = os.path.join(pdir, f'intensity_{ch.replace(" ", "_")}.png')
        plt.savefig(fname, dpi=150)
        print(f"  Plot saved: {fname}")
        plt.show()
        return self

    # ------------------------------------------------------------------
    # Edge detection
    # ------------------------------------------------------------------

    def get_edges(self, x_arr: np.ndarray = None,
                  reflec_arr: np.ndarray = None) -> 'SambaSOTAnalysis':
        """Find device edges from reflection channel.

        Stores self.edges = [x1, x2], self.dev_center, self.width.
        """
        if x_arr is None:
            x_arr = self.x_ref
        if reflec_arr is None:
            if self.reflec_avg is None:
                # Average pos and neg reflections
                r_pos = self.avg_pos.get(self.reflec_ch)
                r_neg = self.avg_neg.get(self.reflec_ch)
                if r_pos is not None and r_neg is not None:
                    reflec_arr = (r_pos + r_neg) / 2.0
                elif r_pos is not None:
                    reflec_arr = r_pos
                elif r_neg is not None:
                    reflec_arr = r_neg
                else:
                    warnings.warn("get_edges: reflection channel not available")
                    return self
            else:
                reflec_arr = self.reflec_avg

        x_arr = np.asarray(x_arr, dtype=float)
        reflec_arr = np.asarray(reflec_arr, dtype=float)

        # Trim 5 points from each end (as in original code)
        x_trim = x_arr[5:-5]
        r_trim = reflec_arr[5:-5]

        try:
            edges, width = analysis_field.find_edges_width(x_trim, r_trim)
        except Exception as e:
            warnings.warn(f"get_edges: find_edges_width failed: {e}")
            return self

        self.edges = edges
        self.dev_center = float(np.mean(edges))
        self.width = float(width)
        print(f"  Edges: {edges[0]:.2f} – {edges[1]:.2f}  "
              f"width = {width:.2f}  center = {self.dev_center:.2f}")
        return self

    # ------------------------------------------------------------------
    # Phase rotation and SOT decomposition
    # ------------------------------------------------------------------

    def _apply_phase(self, x1_arr: np.ndarray, y1_arr: np.ndarray,
                     phase_deg: float) -> np.ndarray:
        """Rotate lock-in quadratures by phase_deg and return real projection."""
        phi = np.deg2rad(phase_deg)
        return x1_arr * np.cos(phi) + y1_arr * np.sin(phi)

    def _compute_theta(self, scan_dict: dict, phase_deg: float) -> np.ndarray | None:
        """Compute Kerr angle from one averaged scan dict."""
        x1 = scan_dict.get(self.x1_ch)
        y1 = scan_dict.get(self.y1_ch)
        if x1 is None or y1 is None:
            return None
        return self._apply_phase(x1, y1, phase_deg) * self.calibration

    def _compute_err(self, scan_dict: dict, phase_deg: float) -> np.ndarray | None:
        """Gaussian error propagation from _std arrays if available."""
        sx = scan_dict.get(self.x1_ch + '_std')
        sy = scan_dict.get(self.y1_ch + '_std')
        if sx is None or sy is None:
            return None
        phi = np.deg2rad(phase_deg)
        return np.sqrt((sx * np.cos(phi))**2 + (sy * np.sin(phi))**2) * abs(self.calibration)

    # ------------------------------------------------------------------
    # evaluate_data
    # ------------------------------------------------------------------

    def evaluate_data(
        self,
        phase: float = None,
        do_plot: str = 'sumdiff',
        ylim=None,
        title: str = None,
    ) -> 'SambaSOTAnalysis':
        """Compute theta_DL and theta_Oe; optionally plot.

        do_plot: 'sumdiff', 'negpos', or 'realimag'.
        Returns self.
        """
        if not self.avg_pos or not self.avg_neg:
            warnings.warn("evaluate_data: averaged data not available, run load_data() first")
            return self

        ph = phase if phase is not None else self.phase

        theta_pos = self._compute_theta(self.avg_pos, ph)
        theta_neg = self._compute_theta(self.avg_neg, ph)

        if theta_pos is None or theta_neg is None:
            warnings.warn("evaluate_data: could not compute theta from lock-in channels")
            return self

        self.theta_DL = (theta_pos - theta_neg) / 2.0
        self.theta_Oe = (theta_pos + theta_neg) / 2.0

        # Errors (Gaussian propagation)
        err_pos = self._compute_err(self.avg_pos, ph)
        err_neg = self._compute_err(self.avg_neg, ph)
        if err_pos is not None and err_neg is not None:
            self.theta_DL_err = np.sqrt(err_pos**2 + err_neg**2) / 2.0
            self.theta_Oe_err = np.sqrt(err_pos**2 + err_neg**2) / 2.0
        else:
            self.theta_DL_err = np.zeros_like(self.theta_DL)
            self.theta_Oe_err = np.zeros_like(self.theta_Oe)

        # Averaged reflection
        r_pos = self.avg_pos.get(self.reflec_ch, np.zeros_like(self.theta_DL))
        r_neg = self.avg_neg.get(self.reflec_ch, np.zeros_like(self.theta_DL))
        self.reflec_avg = (r_pos + r_neg) / 2.0

        x = self.x_ref

        if do_plot:
            self._plot_evaluate(x, theta_pos, theta_neg,
                                self.theta_DL, self.theta_Oe,
                                self.theta_DL_err, self.theta_Oe_err,
                                self.reflec_avg, do_plot, ylim, title)

        return self

    def _plot_evaluate(self, x, theta_pos, theta_neg, theta_DL, theta_Oe,
                       err_DL, err_Oe, reflec, do_plot, ylim, title):
        if do_plot == 'realimag':
            # 2-panel: left = R+(+B), right = R-(-B)
            fig2, (axL, axR) = plt.subplots(1, 2, figsize=(13, 5), sharey=True, dpi=150)
            x1_pos = self.avg_pos.get(self.x1_ch, np.zeros_like(x))
            y1_pos = self.avg_pos.get(self.y1_ch, np.zeros_like(x))
            x1_neg = self.avg_neg.get(self.x1_ch, np.zeros_like(x))
            y1_neg = self.avg_neg.get(self.y1_ch, np.zeros_like(x))
            r_pos  = self.avg_pos.get(self.reflec_ch, np.zeros_like(x))
            r_neg  = self.avg_neg.get(self.reflec_ch, np.zeros_like(x))
            for ax_p, xi, x1i, y1i, ri, lbl in [
                (axL, x, x1_pos, y1_pos, r_pos, r'$R^+$'),
                (axR, x, x1_neg, y1_neg, r_neg, r'$R^-$'),
            ]:
                ax_p.plot(xi, x1i * self.calibration, '-.o', color=_C_DL, label='Real (X1)')
                ax_p.plot(xi, y1i * self.calibration, '-.o', color=_C_OE, label='Imag (Y1)')
                ax_p.axhline(0, color='grey', linewidth=0.7, linestyle='--')
                ax_p.set_title(lbl)
                ax_p.set_xlabel(f'$x$ [{self.x_unit}]')
                ax_p.grid(True, alpha=0.3)
                twin = ax_p.twinx()
                twin.plot(xi, ri, color=_C_REFL, alpha=0.5, linewidth=1.2, label=r'$I_{FL}$')
                twin.set_ylabel(r'$R$ [a.u.]', color=_C_REFL)
                twin.tick_params(axis='y', colors=_C_REFL)
                twin.legend(fontsize=8, loc=4)
            axL.set_ylabel(rf'$\theta_K$ [{self.signal_unit}]')
            axL.legend(fontsize=9)
            t2 = title or self._base_title()
            fig2.suptitle(t2, fontsize=9)
            plt.tight_layout()
            pdir = self._get_plot_dir()
            fname = os.path.join(pdir, 'evaluate_realimag.png')
            fig2.savefig(fname, dpi=150)
            print(f"  Plot saved: {fname}")
            plt.show()
            return  # early return — no ax1 to further modify

        fig, ax1 = plt.subplots(figsize=(9, 5), dpi=150)

        if do_plot == 'sumdiff':
            ax1.errorbar(x, theta_DL, yerr=err_DL,
                         color=_C_DL, fmt='.-', capsize=2, label=r'$\theta_{DL}$')
            ax1.errorbar(x, theta_Oe, yerr=err_Oe,
                         color=_C_OE, fmt='.-', capsize=2, label=r'$\theta_{Oe}$')
        elif do_plot == 'negpos':
            ax1.plot(x, theta_pos, '.-', color=_C_DL, label=r'$\theta(+H)$')
            ax1.plot(x, theta_neg, '.-', color=_C_OE, label=r'$\theta(-H)$')

        ax1.axhline(0, color='grey', linewidth=0.7, linestyle='--')
        ax1.set_xlabel(f'$x$ [{self.x_unit}]')
        ax1.set_ylabel(rf'$\theta_K$ [{self.signal_unit}]')
        ax1.legend(loc='upper left', fontsize=9)
        ax1.grid(True, alpha=0.3)
        if ylim:
            ax1.set_ylim(ylim)

        ax2 = ax1.twinx()
        ax2.plot(x, reflec, color=_C_REFL, alpha=0.55, linewidth=1.2,
                 label='Reflection')
        ax2.set_ylabel('Reflection [a.u.]', color=_C_REFL)
        ax2.tick_params(axis='y', colors=_C_REFL)
        ax2.legend(loc='upper right', fontsize=9)

        t = title or self._base_title()
        ax1.set_title(t, fontsize=9)

        plt.tight_layout()
        pdir = self._get_plot_dir()
        fname = os.path.join(pdir, f'evaluate_{do_plot}.png')
        plt.savefig(fname, dpi=150)
        print(f"  Plot saved: {fname}")
        plt.show()

    # ------------------------------------------------------------------
    # eval_width_and_fit
    # ------------------------------------------------------------------

    def eval_width_and_fit(
        self,
        co: int = 50,
        use_find_impurity: bool = False,
        nice_plot: bool = False,
    ) -> 'SambaSOTAnalysis':
        """Fit Oersted log + DL constant; compute SOT fields in mT."""
        if self.theta_DL is None or self.theta_Oe is None:
            warnings.warn("eval_width_and_fit: run evaluate_data() first")
            return self

        if self.edges is None:
            self.get_edges()
        if self.edges is None:
            warnings.warn("eval_width_and_fit: edge detection failed, cannot fit")
            return self

        x_orig = np.asarray(self.x_ref, dtype=float)
        theta_DL = np.asarray(self.theta_DL, dtype=float)
        theta_Oe = np.asarray(self.theta_Oe, dtype=float)
        err = np.asarray(self.theta_DL_err, dtype=float)
        reflec = np.asarray(self.reflec_avg, dtype=float)

        # Sort
        order = np.argsort(x_orig)
        x = x_orig[order]
        theta_DL = theta_DL[order]
        theta_Oe = theta_Oe[order]
        err = err[order]
        reflec = reflec[order]

        x1_edge, x2_edge = self.edges
        width = float(self.width)

        # Shift x so left edge = 0
        x_shifted = x - x1_edge

        # Device interior mask
        if use_find_impurity:
            try:
                base_mask = (x_shifted > 0) & (x_shifted < width)
                tdl_trim = theta_DL[base_mask]
                imp_mask = analysis_field.find_impurities_peaks(
                    tdl_trim, peakheight=1, do_plot=False)
                use_mask = np.zeros(len(x_shifted), dtype=bool)
                idxs = np.where(base_mask)[0]
                for ii, im in zip(idxs, ~imp_mask):
                    use_mask[ii] = im
            except Exception as e:
                warnings.warn(f"find_impurities_peaks failed: {e}, using plain mask")
                use_mask = (x_shifted > 0) & (x_shifted < width)
        else:
            use_mask = (x_shifted > 0) & (x_shifted < width)

        # Trim 2 points from mask edges
        true_idxs = np.where(use_mask)[0]
        if len(true_idxs) > 4:
            use_mask[true_idxs[:2]] = False
            use_mask[true_idxs[-2:]] = False

        off_mask = ~use_mask

        # Effective current after resistance correction
        Ic = self.current_mA * self.resistance_ratio

        # Fit functions
        def log_fit(xv, A, A0):
            return A0 + A * np.log((width - xv) / xv)

        def const_fit(xv, y0):
            return np.full_like(xv, y0, dtype=float)

        if np.sum(use_mask) < 3:
            warnings.warn("eval_width_and_fit: not enough points in device interior for fit")
            return self

        xfit = x_shifted[use_mask]
        xfit_dl = x_shifted[use_mask]
        oe_fit_data = theta_Oe[use_mask]
        dl_fit_data = theta_DL[use_mask]
        err_fit = np.maximum(err[use_mask], 1e-12)  # avoid zero sigma

        # Oersted log fit
        try:
            p0_log = [np.ptp(oe_fit_data) / 5.0, np.mean(oe_fit_data)]
            popt_log, pcov_log = curve_fit(
                log_fit, xfit, oe_fit_data,
                p0=p0_log, sigma=err_fit, absolute_sigma=True,
                maxfev=5000)
            perr_log = np.sqrt(np.diag(pcov_log))
        except Exception as e:
            warnings.warn(f"Oersted log fit failed: {e}")
            popt_log = [0.0, 0.0]
            perr_log = [0.0, 0.0]

        # DL constant fit
        try:
            popt_const, pcov_const = curve_fit(
                const_fit, xfit_dl, dl_fit_data,
                sigma=err_fit, absolute_sigma=True)
            perr_const = np.sqrt(np.diag(pcov_const))
        except Exception as e:
            warnings.warn(f"DL constant fit failed: {e}")
            popt_const = [np.mean(dl_fit_data)]
            perr_const = [np.std(dl_fit_data)]

        A_oe   = popt_log[0]
        A0_oe  = popt_log[1]
        A_err  = perr_log[0]
        DL_const = popt_const[0]
        DL_err   = perr_const[0]

        # Conversion constant: µrad/mT
        if Ic != 0 and width != 0:
            conconst = A_oe * width / (2.0 * Ic) * 10.0  # µrad/mT
        else:
            conconst = np.nan

        if conconst and conconst != 0 and not np.isnan(conconst):
            conDL = DL_const / conconst   # mT
            conDL_err = abs(conDL) * (abs(DL_err / DL_const) + abs(A_err / A_oe)
                                       if A_oe != 0 else abs(DL_err / DL_const))
        else:
            conDL = np.nan
            conDL_err = np.nan

        self.fit_DL_mT = conDL
        self.fit_DL_error_mT = conDL_err
        self.fit_Oe_A = A_oe
        self.fit_Oe_A0 = A0_oe

        print(f"  Oersted A = {A_oe:.4g} ± {A_err:.3g}  A0 = {A0_oe:.4g}")
        print(f"  DL const  = {DL_const:.4g} ± {DL_err:.4g} µrad")
        print(f"  Conversion constant = {conconst:.4g} µrad/mT")
        print(f"  theta_DL = ({conDL:.4g} ± {conDL_err:.4g}) mT")

        # Plot
        self._plot_fit(x_shifted, theta_DL, theta_Oe, err, reflec,
                       xfit, popt_log, popt_const, use_mask,
                       conconst, conDL, conDL_err, width, nice_plot)

        return self

    def _plot_fit(self, x_shifted, theta_DL, theta_Oe, err, reflec,
                  xfit, popt_log, popt_const, use_mask,
                  conconst, conDL, conDL_err, width, nice_plot):

        def log_fit(xv, A, A0):
            return A0 + A * np.log((width - xv) / xv)

        x_dense = np.linspace(xfit[0], xfit[-1], 300)

        fig, ax = plt.subplots(figsize=(10, 6), dpi=150)

        ax.errorbar(x_shifted, theta_DL, yerr=err,
                    fmt='.-', color=_C_DL, capsize=2, label=r'$\theta_{DL}$')
        ax.errorbar(x_shifted, theta_Oe, yerr=err,
                    fmt='.-', color=_C_OE, capsize=2, label=r'$\theta_{Oe}$')

        # DL fit line
        dl_const_arr = np.full_like(xfit, popt_const[0])
        ax.plot(xfit, dl_const_arr, color=_C_DL_FIT, linewidth=2.5,
                label=f'DL fit = {popt_const[0]:.4g} µrad')

        # Oe log fit line
        try:
            ax.plot(x_dense, log_fit(x_dense, *popt_log), color=_C_OE_FIT, linewidth=2.5,
                    label=f'Oe fit A={popt_log[0]:.4g}')
        except Exception:
            pass

        # Mark unused points
        x_unused = x_shifted[~use_mask & (x_shifted > 0) & (x_shifted < width)]
        if len(x_unused):
            ax.scatter(x_unused, np.full_like(x_unused, np.max(theta_DL) * 0.85),
                       marker='x', color='grey', s=30, zorder=5, label='not used')

        label_conv = (f'conconst = {conconst:.4g} µrad/mT\n'
                      f'$\\theta_{{DL}}$ = ({conDL:.4g} ± {conDL_err:.4g}) mT')
        ax.plot([], [], ' ', label=label_conv)

        ax.axhline(0, color='grey', linewidth=0.7, linestyle='--')
        ax.set_xlabel(f'x [{self.x_unit}]')
        ax.set_ylabel(rf'$\theta_K$ [{self.signal_unit}]')
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.18),
                  ncol=3, fontsize=8, fancybox=True)
        ax.grid(True, alpha=0.3)

        ax2 = ax.twinx()
        ax2.plot(x_shifted, reflec, color=_C_REFL, alpha=0.5, linewidth=1.2,
                 label='Reflection')
        ax2.set_ylabel('Reflection [a.u.]', color=_C_REFL)
        ax2.tick_params(axis='y', colors=_C_REFL)

        t = self._base_title()
        ax.set_title(t, fontsize=8)

        plt.tight_layout()
        pdir = self._get_plot_dir()
        fname = os.path.join(pdir, 'fit_result.png')
        plt.savefig(fname, dpi=150)
        print(f"  Fit plot saved: {fname}")
        plt.show()

        if nice_plot:
            self._nice_plot(x_shifted, theta_DL, theta_Oe, err, reflec,
                            xfit, popt_log, popt_const, width)

    def _nice_plot(self, x_shifted, theta_DL, theta_Oe, err, reflec,
                   xfit, popt_log, popt_const, width):
        def log_fit(xv, A, A0):
            return A0 + A * np.log((width - xv) / xv)

        x_dense = np.linspace(xfit[0], xfit[-1], 300)
        fs = 20

        fig, ax = plt.subplots(figsize=(10, 7), dpi=150)
        ax.errorbar(x_shifted, theta_DL, yerr=err, fmt='.-',
                    color=_C_DL, capsize=2, label=r'$\theta_{DL}$')
        ax.plot(xfit, np.full_like(xfit, popt_const[0]), color=_C_DL_FIT,
                linewidth=3, label='fit const.')
        ax.errorbar(x_shifted, theta_Oe, yerr=err, fmt='.-',
                    color=_C_OE, capsize=2, label=r'$\theta_{Oe}$')
        try:
            ax.plot(x_dense, log_fit(x_dense, *popt_log), color=_C_OE_FIT,
                    linewidth=3, label=r'fit $A\ln\frac{w-x}{x}$')
        except Exception:
            pass

        ax.axhline(0, color='grey', linewidth=0.7, linestyle='--')
        ax.set_xlabel(f'x [{self.x_unit}]', fontsize=fs)
        ax.set_ylabel(rf'$\theta_K$ [{self.signal_unit}]', fontsize=fs)
        ax.legend(loc='upper center', bbox_to_anchor=(0.5, 1.22),
                  ncol=2, fontsize=fs - 4, fancybox=True)
        ax.tick_params(axis='both', labelsize=fs - 2)
        ax.grid(True, alpha=0.3)

        ax2 = ax.twinx()
        ax2.plot(x_shifted, reflec, color=_C_REFL, alpha=0.5, linewidth=1.5)
        ax2.set_ylabel('Reflection [a.u.]', fontsize=fs - 2, color=_C_REFL)
        ax2.tick_params(axis='y', colors=_C_REFL, labelsize=fs - 2)

        plt.tight_layout()
        pdir = self._get_plot_dir()
        fname = os.path.join(pdir, 'nice_plot.png')
        plt.savefig(fname, dpi=150)
        print(f"  Nice plot saved: {fname}")
        plt.show()
