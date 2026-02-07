"""
Set Piece Report Chart
League-wide analysis of set piece contribution to attacking and defensive performance.
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os
import sys
from adjustText import adjust_text

# Import shared utilities
from shared.styles import (
    BG_COLOR, SPINE_COLOR, GRID_COLOR, CBS_BLUE,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    style_axis, style_axis_full_grid, add_cbs_footer
)
from shared.colors import (
    TEAM_COLORS, load_custom_colors, fuzzy_match_team,
    get_team_color, ensure_contrast_with_background
)
from shared.file_utils import get_file_path, get_output_folder


def load_setpiece_data(file_path):
    """Load and process set piece data from CSV."""
    df = pd.read_csv(file_path)

    # Calculate percentages
    df['SP_Goal_Pct'] = (df['SPG'] / df['GF'] * 100).round(1)
    df['SP_GoalAgainst_Pct'] = (df['SPGA'] / df['GA'] * 100).round(1)
    df['SP_xG_Pct'] = (df['SPxG'] / df['xG'] * 100).round(1)
    df['SP_xGA_Pct'] = (df['SPxGA'] / df['xGA'] * 100).round(1)

    # Calculate open play xG (total minus set pieces)
    df['OpenPlay_xG'] = df['xG'] - df['SPxG']
    df['OpenPlay_xGA'] = df['xGA'] - df['SPxGA']

    # Calculate per-game metrics
    games = df['GM']
    df['SPxG_pg'] = (df['SPxG'] / games).round(2)
    df['SPxGA_pg'] = (df['SPxGA'] / games).round(2)
    df['SPG_pg'] = (df['SPG'] / games).round(2)
    df['SPGA_pg'] = (df['SPGA'] / games).round(2)
    df['SPShots_pg'] = (df['SPShots'] / games).round(2)
    df['SPShotsA_pg'] = (df['SPShotsA'] / games).round(2)

    # Handle any infinities or NaN from division
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.fillna(0)

    return df


def get_team_colors_from_df(df):
    """Extract team colors from dataframe, with fallback to database."""
    colors = {}
    for _, row in df.iterrows():
        team = row['Team']
        csv_color = row.get('newestTeamColor', None)

        if pd.notna(csv_color) and csv_color:
            color = csv_color
        else:
            color = get_team_color(team, prompt_if_missing=False)

        # Ensure contrast with dark background
        color = ensure_contrast_with_background(color)
        colors[team] = color

    return colors


def _create_single_panel_figure(title, figsize=(10, 8)):
    """Create a single panel figure with standard styling."""
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)
    fig.suptitle(title, fontsize=16, fontweight='bold', color=TEXT_PRIMARY, y=0.95)
    return fig, ax


def _save_figure(fig, output_folder, filename):
    """Save figure and close it."""
    filepath = os.path.join(output_folder, filename)
    plt.savefig(filepath, dpi=300, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight')
    print(f"  Saved: {filepath}")
    plt.close(fig)
    return filepath


def create_setpiece_attacking_report(df, output_folder=None, league_name=None):
    """Create 4-panel attacking set piece xG report plus individual panels."""

    # Get league name from data if not provided
    if league_name is None:
        league_name = df['leagueName'].iloc[0] if 'leagueName' in df.columns else 'League'

    league_slug = league_name.replace(' ', '_').replace('(', '').replace(')', '')

    # Get team colors
    team_colors = get_team_colors_from_df(df)

    saved_files = []

    # =========================================================================
    # COMBINED 4-PANEL CHART
    # =========================================================================
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    fig.patch.set_facecolor(BG_COLOR)
    fig.suptitle(f'Set Piece Attacking Report: {league_name}',
                 fontsize=20, fontweight='bold', color=TEXT_PRIMARY, y=0.96)

    # Panel 1: Set Piece xG per Game (horizontal bar)
    ax1 = axes[0, 0]
    ax1.set_facecolor(BG_COLOR)

    df_sorted_xg = df.sort_values('SPxG_pg', ascending=True)
    y_pos = range(len(df_sorted_xg))
    colors_xg = [team_colors.get(t, '#888888') for t in df_sorted_xg['Team']]

    bars_xg = ax1.barh(y_pos, df_sorted_xg['SPxG_pg'], color=colors_xg, alpha=0.9)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(df_sorted_xg['teamAbbrevName'], fontsize=9)
    ax1.set_xlabel('Set Piece xG per Game', color=TEXT_PRIMARY, fontsize=11)
    ax1.set_title('Set Piece xG Created (per Game)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)

    for bar, val in zip(bars_xg, df_sorted_xg['SPxG_pg']):
        ax1.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height()/2,
                f'{val:.2f}', va='center', fontsize=8, color=TEXT_PRIMARY)

    avg_spxg = df['SPxG_pg'].mean()
    ax1.axvline(avg_spxg, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
    ax1.text(avg_spxg, len(df) - 0.5, f'Avg: {avg_spxg:.2f}', fontsize=8, color=TEXT_MUTED, ha='center')
    style_axis(ax1)
    ax1.set_xlim(0, df_sorted_xg['SPxG_pg'].max() * 1.15)

    # Panel 2: Set Piece xG % of Total (horizontal bar)
    ax2 = axes[0, 1]
    ax2.set_facecolor(BG_COLOR)

    df_sorted_pct = df.sort_values('SP_xG_Pct', ascending=True)
    y_pos = range(len(df_sorted_pct))
    colors_pct = [team_colors.get(t, '#888888') for t in df_sorted_pct['Team']]

    bars_pct = ax2.barh(y_pos, df_sorted_pct['SP_xG_Pct'], color=colors_pct, alpha=0.9)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(df_sorted_pct['teamAbbrevName'], fontsize=9)
    ax2.set_xlabel('% of Total xG from Set Pieces', color=TEXT_PRIMARY, fontsize=11)
    ax2.set_title('Set Piece Reliance (xG %)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)

    for bar, val in zip(bars_pct, df_sorted_pct['SP_xG_Pct']):
        ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2,
                f'{val:.0f}%', va='center', fontsize=8, color=TEXT_PRIMARY)

    avg_pct = df['SP_xG_Pct'].mean()
    ax2.axvline(avg_pct, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
    ax2.text(avg_pct, len(df) - 0.5, f'Avg: {avg_pct:.0f}%', fontsize=8, color=TEXT_MUTED, ha='center')
    style_axis(ax2)
    ax2.set_xlim(0, df_sorted_pct['SP_xG_Pct'].max() * 1.15)

    # Panel 3: Set Piece Shots vs xG per Game (shot quality)
    ax3 = axes[1, 0]
    ax3.set_facecolor(BG_COLOR)

    texts3 = []
    for _, row in df.iterrows():
        team = row['Team']
        color = team_colors.get(team, '#888888')
        ax3.scatter(row['SPShots_pg'], row['SPxG_pg'],
                   c=color, s=120, alpha=0.9, edgecolors='white', linewidth=0.5)
        abbrev = row.get('teamAbbrevName', team[:3].upper())
        txt = ax3.text(row['SPShots_pg'], row['SPxG_pg'], abbrev,
                      fontsize=8, color=TEXT_PRIMARY, fontweight='bold')
        texts3.append(txt)

    avg_shots = df['SPShots_pg'].mean()
    avg_xg = df['SPxG_pg'].mean()
    ax3.axvline(avg_shots, color=SPINE_COLOR, linestyle='--', alpha=0.5, linewidth=1)
    ax3.axhline(avg_xg, color=SPINE_COLOR, linestyle='--', alpha=0.5, linewidth=1)
    ax3.set_xlabel('Set Piece Shots per Game', color=TEXT_PRIMARY, fontsize=11)
    ax3.set_ylabel('Set Piece xG per Game', color=TEXT_PRIMARY, fontsize=11)
    ax3.set_title('Set Piece Shot Quality (per Game)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)
    style_axis_full_grid(ax3)
    adjust_text(texts3, ax=ax3, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))

    ax3.text(0.03, 0.97, 'Few Shots\nHigh Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
    ax3.text(0.97, 0.97, 'Many Shots\nHigh Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='top', ha='right', alpha=0.7)
    ax3.text(0.03, 0.03, 'Few Shots\nLow Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='bottom', ha='left', alpha=0.7)
    ax3.text(0.97, 0.03, 'Many Shots\nLow Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)

    # Panel 4: Set Piece xG vs Actual Goals per Game (scatter)
    ax4 = axes[1, 1]
    ax4.set_facecolor(BG_COLOR)

    texts4 = []
    for _, row in df.iterrows():
        team = row['Team']
        color = team_colors.get(team, '#888888')
        ax4.scatter(row['SPxG_pg'], row['SPG_pg'],
                   c=color, s=120, alpha=0.9, edgecolors='white', linewidth=0.5)
        abbrev = row.get('teamAbbrevName', team[:3].upper())
        txt = ax4.text(row['SPxG_pg'], row['SPG_pg'], abbrev,
                      fontsize=8, color=TEXT_PRIMARY, fontweight='bold')
        texts4.append(txt)

    max_val = max(df['SPxG_pg'].max(), df['SPG_pg'].max()) * 1.1
    ax4.plot([0, max_val], [0, max_val], color=SPINE_COLOR, linestyle='--', alpha=0.5, linewidth=1)
    ax4.set_xlabel('Set Piece xG per Game', color=TEXT_PRIMARY, fontsize=11)
    ax4.set_ylabel('Set Piece Goals per Game', color=TEXT_PRIMARY, fontsize=11)
    ax4.set_title('Set Piece Finishing (per Game)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)
    style_axis_full_grid(ax4)
    adjust_text(texts4, ax=ax4, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))

    ax4.text(0.03, 0.97, 'Overperforming\n(More goals than xG)', transform=ax4.transAxes,
             fontsize=7, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
    ax4.text(0.97, 0.03, 'Underperforming\n(Fewer goals than xG)', transform=ax4.transAxes,
             fontsize=7, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)

    plt.tight_layout(rect=[0, 0.03, 1, 0.94])
    plt.subplots_adjust(hspace=0.35)
    add_cbs_footer(fig, data_source='Opta/STATS Perform')

    # Save combined chart
    if output_folder:
        main_file = _save_figure(fig, output_folder, f"setpiece_attacking_{league_slug}.png")
        saved_files.append(main_file)

    # =========================================================================
    # INDIVIDUAL PANEL CHARTS
    # =========================================================================
    if output_folder:
        print("\n  Saving individual panels...")

        # Panel 1: xG Created
        fig1, ax1 = _create_single_panel_figure(f'Set Piece xG Created: {league_name}', figsize=(10, 10))
        df_sorted = df.sort_values('SPxG_pg', ascending=True)
        y_pos = range(len(df_sorted))
        colors = [team_colors.get(t, '#888888') for t in df_sorted['Team']]
        bars = ax1.barh(y_pos, df_sorted['SPxG_pg'], color=colors, alpha=0.9)
        ax1.set_yticks(y_pos)
        ax1.set_yticklabels(df_sorted['teamAbbrevName'], fontsize=10)
        ax1.set_xlabel('Set Piece xG per Game', color=TEXT_PRIMARY, fontsize=12)
        for bar, val in zip(bars, df_sorted['SPxG_pg']):
            ax1.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height()/2,
                    f'{val:.2f}', va='center', fontsize=9, color=TEXT_PRIMARY)
        avg = df['SPxG_pg'].mean()
        ax1.axvline(avg, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
        ax1.text(avg, len(df) - 0.5, f'Avg: {avg:.2f}', fontsize=9, color=TEXT_MUTED, ha='center')
        style_axis(ax1)
        ax1.set_xlim(0, df_sorted['SPxG_pg'].max() * 1.15)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig1, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig1, output_folder, f"setpiece_attacking_xg_created_{league_slug}.png"))

        # Panel 2: xG Reliance %
        fig2, ax2 = _create_single_panel_figure(f'Set Piece Reliance: {league_name}', figsize=(10, 10))
        df_sorted = df.sort_values('SP_xG_Pct', ascending=True)
        y_pos = range(len(df_sorted))
        colors = [team_colors.get(t, '#888888') for t in df_sorted['Team']]
        bars = ax2.barh(y_pos, df_sorted['SP_xG_Pct'], color=colors, alpha=0.9)
        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(df_sorted['teamAbbrevName'], fontsize=10)
        ax2.set_xlabel('% of Total xG from Set Pieces', color=TEXT_PRIMARY, fontsize=12)
        for bar, val in zip(bars, df_sorted['SP_xG_Pct']):
            ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2,
                    f'{val:.0f}%', va='center', fontsize=9, color=TEXT_PRIMARY)
        avg = df['SP_xG_Pct'].mean()
        ax2.axvline(avg, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
        ax2.text(avg, len(df) - 0.5, f'Avg: {avg:.0f}%', fontsize=9, color=TEXT_MUTED, ha='center')
        style_axis(ax2)
        ax2.set_xlim(0, df_sorted['SP_xG_Pct'].max() * 1.15)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig2, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig2, output_folder, f"setpiece_attacking_reliance_{league_slug}.png"))

        # Panel 3: Shot Quality
        fig3, ax3 = _create_single_panel_figure(f'Set Piece Shot Quality: {league_name}', figsize=(10, 10))
        texts = []
        for _, row in df.iterrows():
            team = row['Team']
            color = team_colors.get(team, '#888888')
            ax3.scatter(row['SPShots_pg'], row['SPxG_pg'],
                       c=color, s=150, alpha=0.9, edgecolors='white', linewidth=0.5)
            abbrev = row.get('teamAbbrevName', team[:3].upper())
            txt = ax3.text(row['SPShots_pg'], row['SPxG_pg'], abbrev,
                          fontsize=9, color=TEXT_PRIMARY, fontweight='bold')
            texts.append(txt)
        ax3.axvline(df['SPShots_pg'].mean(), color=SPINE_COLOR, linestyle='--', alpha=0.5)
        ax3.axhline(df['SPxG_pg'].mean(), color=SPINE_COLOR, linestyle='--', alpha=0.5)
        ax3.set_xlabel('Set Piece Shots per Game', color=TEXT_PRIMARY, fontsize=12)
        ax3.set_ylabel('Set Piece xG per Game', color=TEXT_PRIMARY, fontsize=12)
        style_axis_full_grid(ax3)
        adjust_text(texts, ax=ax3, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))
        ax3.text(0.03, 0.97, 'Few Shots\nHigh Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
        ax3.text(0.97, 0.97, 'Many Shots\nHigh Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='top', ha='right', alpha=0.7)
        ax3.text(0.03, 0.03, 'Few Shots\nLow Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='bottom', ha='left', alpha=0.7)
        ax3.text(0.97, 0.03, 'Many Shots\nLow Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig3, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig3, output_folder, f"setpiece_attacking_shot_quality_{league_slug}.png"))

        # Panel 4: Finishing
        fig4, ax4 = _create_single_panel_figure(f'Set Piece Finishing: {league_name}', figsize=(10, 10))
        texts = []
        for _, row in df.iterrows():
            team = row['Team']
            color = team_colors.get(team, '#888888')
            ax4.scatter(row['SPxG_pg'], row['SPG_pg'],
                       c=color, s=150, alpha=0.9, edgecolors='white', linewidth=0.5)
            abbrev = row.get('teamAbbrevName', team[:3].upper())
            txt = ax4.text(row['SPxG_pg'], row['SPG_pg'], abbrev,
                          fontsize=9, color=TEXT_PRIMARY, fontweight='bold')
            texts.append(txt)
        max_val = max(df['SPxG_pg'].max(), df['SPG_pg'].max()) * 1.1
        ax4.plot([0, max_val], [0, max_val], color=SPINE_COLOR, linestyle='--', alpha=0.5)
        ax4.set_xlabel('Set Piece xG per Game', color=TEXT_PRIMARY, fontsize=12)
        ax4.set_ylabel('Set Piece Goals per Game', color=TEXT_PRIMARY, fontsize=12)
        style_axis_full_grid(ax4)
        adjust_text(texts, ax=ax4, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))
        ax4.text(0.03, 0.97, 'Overperforming\n(More goals than xG)', transform=ax4.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
        ax4.text(0.97, 0.03, 'Underperforming\n(Fewer goals than xG)', transform=ax4.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig4, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig4, output_folder, f"setpiece_attacking_finishing_{league_slug}.png"))

    return saved_files


def create_setpiece_defensive_report(df, output_folder=None, league_name=None):
    """Create 4-panel defensive set piece xG report plus individual panels."""

    # Get league name from data if not provided
    if league_name is None:
        league_name = df['leagueName'].iloc[0] if 'leagueName' in df.columns else 'League'

    league_slug = league_name.replace(' ', '_').replace('(', '').replace(')', '')

    # Get team colors
    team_colors = get_team_colors_from_df(df)

    saved_files = []

    # =========================================================================
    # COMBINED 4-PANEL CHART
    # =========================================================================
    fig, axes = plt.subplots(2, 2, figsize=(16, 14))
    fig.patch.set_facecolor(BG_COLOR)
    fig.suptitle(f'Set Piece Defensive Report: {league_name}',
                 fontsize=20, fontweight='bold', color=TEXT_PRIMARY, y=0.96)

    # Panel 1: Set Piece xGA per Game (horizontal bar)
    ax1 = axes[0, 0]
    ax1.set_facecolor(BG_COLOR)

    df_sorted_xga = df.sort_values('SPxGA_pg', ascending=False)
    y_pos = range(len(df_sorted_xga))
    colors_xga = [team_colors.get(t, '#888888') for t in df_sorted_xga['Team']]

    bars_xga = ax1.barh(y_pos, df_sorted_xga['SPxGA_pg'], color=colors_xga, alpha=0.9)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(df_sorted_xga['teamAbbrevName'], fontsize=9)
    ax1.set_xlabel('Set Piece xGA per Game', color=TEXT_PRIMARY, fontsize=11)
    ax1.set_title('Set Piece xG Conceded (per Game)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)

    for bar, val in zip(bars_xga, df_sorted_xga['SPxGA_pg']):
        ax1.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height()/2,
                f'{val:.2f}', va='center', fontsize=8, color=TEXT_PRIMARY)

    avg_spxga = df['SPxGA_pg'].mean()
    ax1.axvline(avg_spxga, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
    ax1.text(avg_spxga, len(df) - 0.5, f'Avg: {avg_spxga:.2f}', fontsize=8, color=TEXT_MUTED, ha='center')
    style_axis(ax1)
    ax1.set_xlim(0, df_sorted_xga['SPxGA_pg'].max() * 1.15)

    # Panel 2: Set Piece xGA % of Total (horizontal bar)
    ax2 = axes[0, 1]
    ax2.set_facecolor(BG_COLOR)

    df_sorted_pct = df.sort_values('SP_xGA_Pct', ascending=False)
    y_pos = range(len(df_sorted_pct))
    colors_pct = [team_colors.get(t, '#888888') for t in df_sorted_pct['Team']]

    bars_pct = ax2.barh(y_pos, df_sorted_pct['SP_xGA_Pct'], color=colors_pct, alpha=0.9)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(df_sorted_pct['teamAbbrevName'], fontsize=9)
    ax2.set_xlabel('% of Total xGA from Set Pieces', color=TEXT_PRIMARY, fontsize=11)
    ax2.set_title('Set Piece Vulnerability (xGA %)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)

    for bar, val in zip(bars_pct, df_sorted_pct['SP_xGA_Pct']):
        ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2,
                f'{val:.0f}%', va='center', fontsize=8, color=TEXT_PRIMARY)

    avg_pct = df['SP_xGA_Pct'].mean()
    ax2.axvline(avg_pct, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
    ax2.text(avg_pct, len(df) - 0.5, f'Avg: {avg_pct:.0f}%', fontsize=8, color=TEXT_MUTED, ha='center')
    style_axis(ax2)
    ax2.set_xlim(0, df_sorted_pct['SP_xGA_Pct'].max() * 1.15)

    # Panel 3: Set Piece Shots Faced vs xGA per Game
    ax3 = axes[1, 0]
    ax3.set_facecolor(BG_COLOR)

    texts3 = []
    for _, row in df.iterrows():
        team = row['Team']
        color = team_colors.get(team, '#888888')
        ax3.scatter(row['SPShotsA_pg'], row['SPxGA_pg'],
                   c=color, s=120, alpha=0.9, edgecolors='white', linewidth=0.5)
        abbrev = row.get('teamAbbrevName', team[:3].upper())
        txt = ax3.text(row['SPShotsA_pg'], row['SPxGA_pg'], abbrev,
                      fontsize=8, color=TEXT_PRIMARY, fontweight='bold')
        texts3.append(txt)

    ax3.axvline(df['SPShotsA_pg'].mean(), color=SPINE_COLOR, linestyle='--', alpha=0.5)
    ax3.axhline(df['SPxGA_pg'].mean(), color=SPINE_COLOR, linestyle='--', alpha=0.5)
    ax3.set_xlabel('Set Piece Shots Faced per Game', color=TEXT_PRIMARY, fontsize=11)
    ax3.set_ylabel('Set Piece xGA per Game', color=TEXT_PRIMARY, fontsize=11)
    ax3.set_title('Set Piece Shot Quality Faced (per Game)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)
    style_axis_full_grid(ax3)
    adjust_text(texts3, ax=ax3, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))

    ax3.text(0.03, 0.97, 'Few Shots\nHigh Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
    ax3.text(0.97, 0.97, 'Many Shots\nHigh Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='top', ha='right', alpha=0.7)
    ax3.text(0.03, 0.03, 'Few Shots\nLow Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='bottom', ha='left', alpha=0.7)
    ax3.text(0.97, 0.03, 'Many Shots\nLow Quality', transform=ax3.transAxes,
             fontsize=7, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)

    # Panel 4: Set Piece xGA vs Actual Goals Conceded
    ax4 = axes[1, 1]
    ax4.set_facecolor(BG_COLOR)

    texts4 = []
    for _, row in df.iterrows():
        team = row['Team']
        color = team_colors.get(team, '#888888')
        ax4.scatter(row['SPxGA_pg'], row['SPGA_pg'],
                   c=color, s=120, alpha=0.9, edgecolors='white', linewidth=0.5)
        abbrev = row.get('teamAbbrevName', team[:3].upper())
        txt = ax4.text(row['SPxGA_pg'], row['SPGA_pg'], abbrev,
                      fontsize=8, color=TEXT_PRIMARY, fontweight='bold')
        texts4.append(txt)

    max_val = max(df['SPxGA_pg'].max(), df['SPGA_pg'].max()) * 1.1
    ax4.plot([0, max_val], [0, max_val], color=SPINE_COLOR, linestyle='--', alpha=0.5)
    ax4.set_xlabel('Set Piece xGA per Game', color=TEXT_PRIMARY, fontsize=11)
    ax4.set_ylabel('Set Piece Goals Conceded per Game', color=TEXT_PRIMARY, fontsize=11)
    ax4.set_title('Set Piece Defense (per Game)', color=TEXT_PRIMARY, fontsize=13, fontweight='bold', pad=10)
    style_axis_full_grid(ax4)
    adjust_text(texts4, ax=ax4, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))

    ax4.text(0.03, 0.97, 'Unlucky\n(Conceding more than xGA)', transform=ax4.transAxes,
             fontsize=7, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
    ax4.text(0.97, 0.03, 'Lucky\n(Conceding less than xGA)', transform=ax4.transAxes,
             fontsize=7, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)

    plt.tight_layout(rect=[0, 0.03, 1, 0.94])
    plt.subplots_adjust(hspace=0.35)
    add_cbs_footer(fig, data_source='Opta/STATS Perform')

    # Save combined chart
    if output_folder:
        main_file = _save_figure(fig, output_folder, f"setpiece_defensive_{league_slug}.png")
        saved_files.append(main_file)

    # =========================================================================
    # INDIVIDUAL PANEL CHARTS
    # =========================================================================
    if output_folder:
        print("\n  Saving individual panels...")

        # Panel 1: xGA Conceded
        fig1, ax1 = _create_single_panel_figure(f'Set Piece xG Conceded: {league_name}', figsize=(10, 10))
        df_sorted = df.sort_values('SPxGA_pg', ascending=False)
        y_pos = range(len(df_sorted))
        colors = [team_colors.get(t, '#888888') for t in df_sorted['Team']]
        bars = ax1.barh(y_pos, df_sorted['SPxGA_pg'], color=colors, alpha=0.9)
        ax1.set_yticks(y_pos)
        ax1.set_yticklabels(df_sorted['teamAbbrevName'], fontsize=10)
        ax1.set_xlabel('Set Piece xGA per Game', color=TEXT_PRIMARY, fontsize=12)
        for bar, val in zip(bars, df_sorted['SPxGA_pg']):
            ax1.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height()/2,
                    f'{val:.2f}', va='center', fontsize=9, color=TEXT_PRIMARY)
        avg = df['SPxGA_pg'].mean()
        ax1.axvline(avg, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
        ax1.text(avg, len(df) - 0.5, f'Avg: {avg:.2f}', fontsize=9, color=TEXT_MUTED, ha='center')
        style_axis(ax1)
        ax1.set_xlim(0, df_sorted['SPxGA_pg'].max() * 1.15)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig1, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig1, output_folder, f"setpiece_defensive_xga_conceded_{league_slug}.png"))

        # Panel 2: Vulnerability %
        fig2, ax2 = _create_single_panel_figure(f'Set Piece Vulnerability: {league_name}', figsize=(10, 10))
        df_sorted = df.sort_values('SP_xGA_Pct', ascending=False)
        y_pos = range(len(df_sorted))
        colors = [team_colors.get(t, '#888888') for t in df_sorted['Team']]
        bars = ax2.barh(y_pos, df_sorted['SP_xGA_Pct'], color=colors, alpha=0.9)
        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(df_sorted['teamAbbrevName'], fontsize=10)
        ax2.set_xlabel('% of Total xGA from Set Pieces', color=TEXT_PRIMARY, fontsize=12)
        for bar, val in zip(bars, df_sorted['SP_xGA_Pct']):
            ax2.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height()/2,
                    f'{val:.0f}%', va='center', fontsize=9, color=TEXT_PRIMARY)
        avg = df['SP_xGA_Pct'].mean()
        ax2.axvline(avg, color=TEXT_MUTED, linestyle='--', alpha=0.7, linewidth=1)
        ax2.text(avg, len(df) - 0.5, f'Avg: {avg:.0f}%', fontsize=9, color=TEXT_MUTED, ha='center')
        style_axis(ax2)
        ax2.set_xlim(0, df_sorted['SP_xGA_Pct'].max() * 1.15)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig2, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig2, output_folder, f"setpiece_defensive_vulnerability_{league_slug}.png"))

        # Panel 3: Shot Quality Faced
        fig3, ax3 = _create_single_panel_figure(f'Set Piece Shot Quality Faced: {league_name}', figsize=(10, 10))
        texts = []
        for _, row in df.iterrows():
            team = row['Team']
            color = team_colors.get(team, '#888888')
            ax3.scatter(row['SPShotsA_pg'], row['SPxGA_pg'],
                       c=color, s=150, alpha=0.9, edgecolors='white', linewidth=0.5)
            abbrev = row.get('teamAbbrevName', team[:3].upper())
            txt = ax3.text(row['SPShotsA_pg'], row['SPxGA_pg'], abbrev,
                          fontsize=9, color=TEXT_PRIMARY, fontweight='bold')
            texts.append(txt)
        ax3.axvline(df['SPShotsA_pg'].mean(), color=SPINE_COLOR, linestyle='--', alpha=0.5)
        ax3.axhline(df['SPxGA_pg'].mean(), color=SPINE_COLOR, linestyle='--', alpha=0.5)
        ax3.set_xlabel('Set Piece Shots Faced per Game', color=TEXT_PRIMARY, fontsize=12)
        ax3.set_ylabel('Set Piece xGA per Game', color=TEXT_PRIMARY, fontsize=12)
        style_axis_full_grid(ax3)
        adjust_text(texts, ax=ax3, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))
        ax3.text(0.03, 0.97, 'Few Shots\nHigh Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
        ax3.text(0.97, 0.97, 'Many Shots\nHigh Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='top', ha='right', alpha=0.7)
        ax3.text(0.03, 0.03, 'Few Shots\nLow Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='bottom', ha='left', alpha=0.7)
        ax3.text(0.97, 0.03, 'Many Shots\nLow Quality', transform=ax3.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig3, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig3, output_folder, f"setpiece_defensive_shot_quality_{league_slug}.png"))

        # Panel 4: Defense Performance
        fig4, ax4 = _create_single_panel_figure(f'Set Piece Defense Performance: {league_name}', figsize=(10, 10))
        texts = []
        for _, row in df.iterrows():
            team = row['Team']
            color = team_colors.get(team, '#888888')
            ax4.scatter(row['SPxGA_pg'], row['SPGA_pg'],
                       c=color, s=150, alpha=0.9, edgecolors='white', linewidth=0.5)
            abbrev = row.get('teamAbbrevName', team[:3].upper())
            txt = ax4.text(row['SPxGA_pg'], row['SPGA_pg'], abbrev,
                          fontsize=9, color=TEXT_PRIMARY, fontweight='bold')
            texts.append(txt)
        max_val = max(df['SPxGA_pg'].max(), df['SPGA_pg'].max()) * 1.1
        ax4.plot([0, max_val], [0, max_val], color=SPINE_COLOR, linestyle='--', alpha=0.5)
        ax4.set_xlabel('Set Piece xGA per Game', color=TEXT_PRIMARY, fontsize=12)
        ax4.set_ylabel('Set Piece Goals Conceded per Game', color=TEXT_PRIMARY, fontsize=12)
        style_axis_full_grid(ax4)
        adjust_text(texts, ax=ax4, arrowprops=dict(arrowstyle='-', color=SPINE_COLOR, lw=0.5))
        ax4.text(0.03, 0.97, 'Unlucky\n(Conceding more than xGA)', transform=ax4.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='top', ha='left', alpha=0.7)
        ax4.text(0.97, 0.03, 'Lucky\n(Conceding less than xGA)', transform=ax4.transAxes,
                 fontsize=8, color=TEXT_MUTED, va='bottom', ha='right', alpha=0.7)
        plt.tight_layout(rect=[0, 0.02, 1, 0.93])
        add_cbs_footer(fig4, data_source='Opta/STATS Perform')
        saved_files.append(_save_figure(fig4, output_folder, f"setpiece_defensive_performance_{league_slug}.png"))

    return saved_files


def create_setpiece_report(df, output_folder=None, league_name=None, report_type='both'):
    """Create set piece report(s).

    Args:
        df: DataFrame with set piece data
        output_folder: Folder to save charts
        league_name: Name of the league (auto-detected if None)
        report_type: 'attacking', 'defensive', or 'both'

    Returns:
        List of saved file paths
    """
    all_files = []

    if report_type in ['attacking', 'both']:
        print("\nGenerating Attacking Set Piece Report...")
        files = create_setpiece_attacking_report(df, output_folder, league_name)
        all_files.extend(files)

    if report_type in ['defensive', 'both']:
        print("\nGenerating Defensive Set Piece Report...")
        files = create_setpiece_defensive_report(df, output_folder, league_name)
        all_files.extend(files)

    print(f"\n[OK] Generated {len(all_files)} charts")
    return all_files


def _get_downloads_folder():
    """Get the user's Downloads folder path."""
    if sys.platform == 'win32':
        return os.path.join(os.path.expanduser('~'), 'Downloads')
    elif sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~'), 'Downloads')
    else:
        return os.path.join(os.path.expanduser('~'), 'Downloads')


def main():
    """Main entry point for set piece report chart."""
    print("\n" + "=" * 60)
    print("SET PIECE REPORT")
    print("=" * 60)
    print("Analyzes set piece contribution across all teams in a league.")

    file_path = get_file_path("TruMedia Set Piece Report CSV")
    if not file_path:
        return

    # Ask which report to generate
    print("\nWhich report would you like to generate?")
    print("  1. Attacking (set piece xG created)")
    print("  2. Defensive (set piece xG conceded)")
    print("  3. Both")

    while True:
        choice = input("Select (1-3, default=3): ").strip()
        if choice == '' or choice == '3':
            report_type = 'both'
            break
        elif choice == '1':
            report_type = 'attacking'
            break
        elif choice == '2':
            report_type = 'defensive'
            break
        print("Invalid choice. Please enter 1, 2, or 3.")

    output_folder = get_output_folder()

    # Load and process data
    print("\nLoading data...")
    df = load_setpiece_data(file_path)
    print(f"  Loaded {len(df)} teams")

    # Create chart(s)
    saved_files = create_setpiece_report(df, output_folder, report_type=report_type)

    # Open the main combined chart
    if saved_files:
        main_chart = saved_files[0]
        print(f"\nOpening: {main_chart}")
        try:
            os.startfile(main_chart)
        except Exception as e:
            print(f"Could not open chart: {e}")


def run(config):
    """Run function for launcher/GUI integration."""
    file_path = config.get('file_path')
    output_folder = config.get('output_folder') or _get_downloads_folder()
    report_type = config.get('report_type', 'both')

    df = load_setpiece_data(file_path)
    saved_files = create_setpiece_report(df, output_folder, report_type=report_type)

    return saved_files


if __name__ == "__main__":
    main()
