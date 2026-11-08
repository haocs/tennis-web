/* Authoritative original-time kept/gap timeline and single-version clock projection. */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CutReviewCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';

  const finite = x => typeof x === 'number' && Number.isFinite(x);
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const toMs = sec => Math.round(Number(sec) * 1000);
  const toSec = ms => Math.round(Number(ms)) / 1000;

  function frameToleranceMs(fps = 30) {
    const rate = finite(fps) && fps > 0 ? fps : 30;
    return Math.max(45, 2 * Math.ceil(1000 / rate));
  }

  function withinFrameTolerance(actualMs, expectedMs, fps = 30) {
    if (!finite(actualMs) || !finite(expectedMs)) return false;
    return Math.abs(actualMs - expectedMs) <= frameToleranceMs(fps);
  }

  function effectiveClipDurationSeconds(clip, runtimeDurationSeconds = null, fps = 30) {
    const manifestDuration = Number(clip?.duration_seconds);
    const fallback = finite(manifestDuration) && manifestDuration > 0
      ? manifestDuration
      : Math.max(0, Number(clip?.original_end_seconds || 0) - Number(clip?.original_start_seconds || 0));
    if (finite(runtimeDurationSeconds) && runtimeDurationSeconds > 0) {
      const toleranceSec = frameToleranceMs(fps) / 1000;
      if (Math.abs(runtimeDurationSeconds - fallback) <= toleranceSec + 1e-6) {
        return runtimeDurationSeconds;
      }
    }
    return fallback;
  }

  function normalizeClip(clip, indexFallback, totalDurationMs = null, fps = 30) {
    const rawStartSec = Number(clip?.original_start_seconds);
    const rawEndSec = Number(clip?.original_end_seconds);
    if (!finite(rawStartSec) || !finite(rawEndSec)) {
      throw new Error('Clip original timestamps must be finite numbers');
    }
    const rawStartMs = toMs(rawStartSec);
    const rawEndMs = toMs(rawEndSec);
    const tolMs = frameToleranceMs(fps);
    if (rawStartMs < -tolMs || rawStartMs >= rawEndMs) {
      throw new Error('Clip original start/end bounds are invalid');
    }
    if (finite(totalDurationMs) && rawEndMs > totalDurationMs + tolMs) {
      throw new Error('Clip original end exceeds source duration beyond frame tolerance');
    }
    const startMs = finite(totalDurationMs)
      ? clamp(rawStartMs, 0, totalDurationMs)
      : Math.max(0, rawStartMs);
    const endMs = finite(totalDurationMs)
      ? clamp(rawEndMs, 0, totalDurationMs)
      : Math.max(startMs, rawEndMs);
    if (startMs >= endMs) {
      throw new Error('Clip original bounds collapse after clamping to source duration');
    }
    const durationSeconds = effectiveClipDurationSeconds(clip, null, fps);
    const playbackStartSeconds = finite(Number(clip.playback_start_seconds))
      ? Number(clip.playback_start_seconds)
      : 0;
    const playbackEndSeconds = finite(Number(clip.playback_end_seconds))
      ? Number(clip.playback_end_seconds)
      : playbackStartSeconds + durationSeconds;
    return {
      ...clip,
      id: clip.id,
      index: Number.isInteger(clip.index) ? clip.index : indexFallback,
      startMs,
      endMs,
      startSeconds: startMs / 1000,
      endSeconds: endMs / 1000,
      originalSpanMs: endMs - startMs,
      originalSpanSeconds: (endMs - startMs) / 1000,
      durationSeconds,
      playbackStartSeconds,
      playbackEndSeconds,
    };
  }

  function buildTimeline(data, options = {}) {
    const rawDurationSec = Number(data?.source?.duration_seconds ?? data?.duration_seconds ?? 0);
    if (!finite(rawDurationSec) || rawDurationSec < 0) {
      throw new Error('Original source duration must be a non-negative finite number');
    }
    const totalDurationMs = toMs(rawDurationSec);
    const fps = Number(options.fps ?? data?.video?.fps ?? data?.source?.fps ?? 30);
    const tolMs = frameToleranceMs(fps);
    const rawClips = Array.isArray(data?.clips) ? data.clips : [];
    const clips = rawClips
      .map((c, idx) => normalizeClip(c, idx + 1, totalDurationMs, fps))
      .sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs || (a.index - b.index));

    for (let i = 0; i + 1 < clips.length; i++) {
      const cur = clips[i];
      const next = clips[i + 1];
      if (cur.endMs > next.startMs) {
        if (cur.endMs - next.startMs <= tolMs) {
          cur.endMs = next.startMs;
          cur.endSeconds = cur.endMs / 1000;
          cur.originalSpanMs = cur.endMs - cur.startMs;
          cur.originalSpanSeconds = cur.originalSpanMs / 1000;
          if (cur.startMs >= cur.endMs) {
            throw new Error('Overlapping clip bounds collapse after tolerance clamping');
          }
        } else {
          throw new Error('Clip original intervals overlap beyond frame tolerance');
        }
      }
    }

    const gaps = [];
    const touchingBoundaries = [];
    const segments = [];

    const pushGap = (startMs, endMs, position, prevClip, nextClip) => {
      const gapIndex = gaps.length + 1;
      const gap = {
        kind: 'gap',
        id: `gap-${gapIndex}`,
        gapIndex,
        startMs,
        endMs,
        startSeconds: startMs / 1000,
        endSeconds: endMs / 1000,
        durationMs: endMs - startMs,
        durationSeconds: (endMs - startMs) / 1000,
        position,
        prevClipId: prevClip ? prevClip.id : null,
        prevClipIndex: prevClip ? prevClip.index : null,
        nextClipId: nextClip ? nextClip.id : null,
        nextClipIndex: nextClip ? nextClip.index : null,
      };
      gaps.push(gap);
      segments.push(gap);
    };

    if (clips.length === 0) {
      if (totalDurationMs > 0) {
        pushGap(0, totalDurationMs, 'full', null, null);
      }
    } else {
      if (clips[0].startMs > 0) {
        pushGap(0, clips[0].startMs, 'leading', null, clips[0]);
      }
      for (let i = 0; i < clips.length; i++) {
        const cur = clips[i];
        segments.push({
          kind: 'clip',
          id: cur.id,
          clipId: cur.id,
          clipIndex: cur.index,
          startMs: cur.startMs,
          endMs: cur.endMs,
          startSeconds: cur.startSeconds,
          endSeconds: cur.endSeconds,
          durationMs: cur.originalSpanMs,
          durationSeconds: cur.originalSpanSeconds,
          encodedDurationSeconds: cur.durationSeconds,
          playbackStartSeconds: cur.playbackStartSeconds,
          playbackEndSeconds: cur.playbackEndSeconds,
          clip: cur,
        });
        if (i + 1 < clips.length) {
          const next = clips[i + 1];
          if (cur.endMs < next.startMs) {
            pushGap(cur.endMs, next.startMs, 'internal', cur, next);
          } else if (cur.endMs === next.startMs) {
            touchingBoundaries.push({
              atMs: cur.endMs,
              atSeconds: cur.endMs / 1000,
              leftClipId: cur.id,
              rightClipId: next.id,
            });
          }
        }
      }
      const last = clips[clips.length - 1];
      if (last.endMs < totalDurationMs) {
        pushGap(last.endMs, totalDurationMs, 'trailing', last, null);
      }
    }

    const keptDurationMs = clips.reduce((sum, c) => sum + c.originalSpanMs, 0);
    const removedDurationMs = gaps.reduce((sum, g) => sum + g.durationMs, 0);

    return {
      totalDurationMs,
      totalDurationSeconds: totalDurationMs / 1000,
      keptDurationMs,
      keptDurationSeconds: keptDurationMs / 1000,
      removedDurationMs,
      removedDurationSeconds: removedDurationMs / 1000,
      clips,
      gaps,
      touchingBoundaries,
      segments,
    };
  }

  function ensureTimeline(timelineOrData) {
    if (timelineOrData && Array.isArray(timelineOrData.clips) && Array.isArray(timelineOrData.gaps) && finite(timelineOrData.totalDurationMs)) {
      return timelineOrData;
    }
    return buildTimeline(timelineOrData || {});
  }

  function projectOriginalToPlayback(timelineOrData, origMsInput, options = {}) {
    const tl = ensureTimeline(timelineOrData);
    const D = tl.totalDurationMs;
    const origMs = clamp(Math.round(Number(origMsInput) || 0), 0, D);
    const fps = options.fps || 30;
    const runtimeByClip = options.runtimeDurations || {};

    if (D === 0) {
      return {
        kind: 'empty',
        originalMs: 0,
        originalSeconds: 0,
        atEndOfRecording: true,
      };
    }

    // Endpoint rule: origMs === D
    if (origMs === D) {
      const lastClip = tl.clips.length ? tl.clips[tl.clips.length - 1] : null;
      if (lastClip && lastClip.endMs === D) {
        const effDur = effectiveClipDurationSeconds(
          lastClip,
          options.clipId === lastClip.id ? options.runtimeDurationSeconds : runtimeByClip[lastClip.id],
          fps
        );
        return {
          kind: 'clip',
          clip: lastClip,
          clipId: lastClip.id,
          clipIndex: lastClip.index,
          alpha: 1,
          localSeconds: effDur,
          joinedSeconds: lastClip.playbackEndSeconds,
          originalMs: D,
          originalSeconds: D / 1000,
          atEndOfRecording: true,
        };
      }
      const lastGap = tl.gaps.length ? tl.gaps[tl.gaps.length - 1] : null;
      if (lastGap) {
        return {
          kind: 'gap',
          gap: lastGap,
          gapIndex: lastGap.gapIndex,
          gapOffsetMs: D - lastGap.startMs,
          originalMs: D,
          originalSeconds: D / 1000,
          prevClipId: lastGap.prevClipId,
          nextClipId: lastGap.nextClipId,
          atEndOfRecording: true,
        };
      }
    }

    // Half-open clip membership: [s_i, e_i)
    for (const clip of tl.clips) {
      if (origMs >= clip.startMs && origMs < clip.endMs) {
        const spanMs = clip.endMs - clip.startMs;
        const alpha = spanMs > 0 ? clamp((origMs - clip.startMs) / spanMs, 0, 1) : 0;
        const effDur = effectiveClipDurationSeconds(
          clip,
          options.clipId === clip.id ? options.runtimeDurationSeconds : runtimeByClip[clip.id],
          fps
        );
        const joinedSpan = Math.max(0, clip.playbackEndSeconds - clip.playbackStartSeconds);
        return {
          kind: 'clip',
          clip,
          clipId: clip.id,
          clipIndex: clip.index,
          alpha,
          localSeconds: alpha * effDur,
          joinedSeconds: clip.playbackStartSeconds + alpha * joinedSpan,
          originalMs: origMs,
          originalSeconds: origMs / 1000,
          atEndOfRecording: false,
        };
      }
    }

    // Half-open gap membership: [a_j, b_j)
    for (const gap of tl.gaps) {
      if (origMs >= gap.startMs && origMs < gap.endMs) {
        return {
          kind: 'gap',
          gap,
          gapIndex: gap.gapIndex,
          gapOffsetMs: origMs - gap.startMs,
          originalMs: origMs,
          originalSeconds: origMs / 1000,
          prevClipId: gap.prevClipId,
          nextClipId: gap.nextClipId,
          atEndOfRecording: false,
        };
      }
    }

    return {
      kind: 'gap',
      gap: tl.gaps[0] || null,
      gapIndex: tl.gaps[0]?.gapIndex || null,
      gapOffsetMs: 0,
      originalMs: origMs,
      originalSeconds: origMs / 1000,
      atEndOfRecording: origMs === D,
    };
  }

  function projectLocalToOriginal(clipInput, localSecondsInput, options = {}) {
    const clip = clipInput && finite(clipInput.startMs) ? clipInput : normalizeClip(clipInput || {}, 1);
    const effDur = effectiveClipDurationSeconds(clip, options.runtimeDurationSeconds, options.fps || 30);
    const localSec = clamp(Number(localSecondsInput) || 0, 0, Math.max(0, effDur));
    const beta = effDur > 0 ? clamp(localSec / effDur, 0, 1) : 0;
    const originalMs = Math.round(clip.startMs + beta * (clip.endMs - clip.startMs));
    const joinedSpan = Math.max(0, clip.playbackEndSeconds - clip.playbackStartSeconds);
    return {
      originalMs,
      originalSeconds: originalMs / 1000,
      beta,
      localSeconds: localSec,
      joinedSeconds: clip.playbackStartSeconds + beta * joinedSpan,
    };
  }

  function adjacentGapsForClip(timelineOrData, clipId) {
    const tl = ensureTimeline(timelineOrData);
    const prevGap = tl.gaps.find(g => g.nextClipId === clipId) || null;
    const nextGap = tl.gaps.find(g => g.prevClipId === clipId) || null;
    return { prevGap, nextGap };
  }

  function validateSuggestedBounds(clipInput, suggestedStartMs, suggestedEndMs, sourceDurationMs) {
    const clip = clipInput && finite(clipInput.startMs) ? clipInput : normalizeClip(clipInput || {}, 1);
    const D = Math.round(Number(sourceDurationMs) || 0);
    const hasStart = suggestedStartMs !== null && suggestedStartMs !== undefined && suggestedStartMs !== '';
    const hasEnd = suggestedEndMs !== null && suggestedEndMs !== undefined && suggestedEndMs !== '';
    if (!hasStart && !hasEnd) {
      return { valid: true, suggested_start_ms: null, suggested_end_ms: null };
    }
    const startMs = hasStart ? Number(suggestedStartMs) : null;
    const endMs = hasEnd ? Number(suggestedEndMs) : null;
    if (hasStart && (!Number.isInteger(startMs) || startMs < 0 || startMs >= D)) {
      return { valid: false, error: 'Suggested start must be within the original recording' };
    }
    if (hasEnd && (!Number.isInteger(endMs) || endMs <= 0 || endMs > D)) {
      return { valid: false, error: 'Suggested end must be within the original recording' };
    }
    const effectiveStartMs = hasStart ? startMs : clip.startMs;
    const effectiveEndMs = hasEnd ? endMs : clip.endMs;
    if (effectiveStartMs >= effectiveEndMs) {
      return { valid: false, error: 'Suggested end must be after suggested start' };
    }
    return {
      valid: true,
      suggested_start_ms: startMs,
      suggested_end_ms: endMs,
      effective_start_ms: effectiveStartMs,
      effective_end_ms: effectiveEndMs,
    };
  }

  function validateGapInterval(timelineOrData, startMsInput, endMsInput) {
    const tl = ensureTimeline(timelineOrData);
    const startMs = Math.round(Number(startMsInput));
    const endMs = Math.round(Number(endMsInput));
    if (!finite(startMs) || !finite(endMs) || startMs < 0 || endMs > tl.totalDurationMs || startMs >= endMs) {
      return { valid: false, error: 'Choose a valid start and end within the removed gap' };
    }
    const containingGap = tl.gaps.find(g => startMs >= g.startMs && endMs <= g.endMs);
    if (!containingGap) {
      return { valid: false, error: 'Missed-play interval must be wholly inside a removed gap' };
    }
    return {
      valid: true,
      gap: containingGap,
      startMs,
      endMs,
      startSeconds: toSec(startMs),
      endSeconds: toSec(endMs),
    };
  }

  return {
    frameToleranceMs,
    withinFrameTolerance,
    effectiveClipDurationSeconds,
    buildTimeline,
    projectOriginalToPlayback,
    projectLocalToOriginal,
    adjacentGapsForClip,
    validateSuggestedBounds,
    validateGapInterval,
  };
});
