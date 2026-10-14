// ============================================================
// platoon_sim.js
// Core simulation engine (no UI code here).
//
// Provides: makeProjector, snapToRoads, resamplePath,
//           OpenPath, ClosedPath, Vehicle, Platoon
//
// These are plain classic-script top-level declarations, so
// they are automatically visible to any <script> tag loaded
// AFTER this one in the same HTML page (no import needed).
// ============================================================

// ---------------- Coordinate projection (local flat-earth, meters) ----------------
function makeProjector(lat0, lon0) {
  const lat0Rad = lat0 * Math.PI / 180;
  const mPerDegLat = 111320;
  const mPerDegLon = 111320 * Math.cos(lat0Rad);
  return {
    toXY(lat, lon) { return { x: (lon - lon0) * mPerDegLon, y: (lat - lat0) * mPerDegLat }; },
    toLatLon(x, y) { return { lat: lat0 + y / mPerDegLat, lon: lon0 + x / mPerDegLon }; }
  };
}

// ---------------- Snap to roads (free OSRM public server) ----------------
async function snapToRoads(points, closeLoop) {
  const seq = closeLoop ? points.concat([points[0]]) : points;
  const coordStr = seq.map(p => `${p.lng},${p.lat}`).join(';');
  const url = `https://router.project-osrm.org/route/v1/driving/${coordStr}?geometries=geojson&overview=full`;
  const res = await fetch(url);
  const data = await res.json();
  if (data.code !== 'Ok') throw new Error('OSRM error: ' + data.code);
  const route = data.routes[0];
  return {
    coords: route.geometry.coordinates.map(([lng, lat]) => ({ lat, lng })),
    distance_m: route.distance,   // real road distance, from OSRM
    duration_s: route.duration    // OSRM's free-flow drive time estimate
  };
}

// ---------------- Path resampling ----------------
function resamplePath(xyPoints, spacing) {
  let cum = [0];
  for (let i = 1; i < xyPoints.length; i++) {
    const d = Math.hypot(xyPoints[i].x - xyPoints[i - 1].x, xyPoints[i].y - xyPoints[i - 1].y);
    cum.push(cum[i - 1] + d);
  }
  const total = cum[cum.length - 1];
  const n = Math.max(Math.floor(total / spacing), 2);
  const result = [];
  let segIdx = 0;
  for (let k = 0; k <= n; k++) {
    const targetDist = (k / n) * total;
    while (segIdx < cum.length - 2 && cum[segIdx + 1] < targetDist) segIdx++;
    const d0 = cum[segIdx], d1 = cum[segIdx + 1];
    const t = d1 > d0 ? (targetDist - d0) / (d1 - d0) : 0;
    const p0 = xyPoints[segIdx], p1 = xyPoints[segIdx + 1];
    result.push({ x: p0.x + t * (p1.x - p0.x), y: p0.y + t * (p1.y - p0.y) });
  }
  return { points: result, total };
}

// ---------------- Open (point-to-point) path lookup ----------------
class OpenPath {
  constructor(pts) {
    this.x = pts.map(p => p.x);
    this.y = pts.map(p => p.y);
    this.n = this.x.length;
    this.cumDist = [0];
    for (let i = 1; i < this.n; i++) {
      this.cumDist.push(this.cumDist[i - 1] + Math.hypot(this.x[i] - this.x[i - 1], this.y[i] - this.y[i - 1]));
    }
    this.length = this.cumDist[this.n - 1];
    this.segHeading = [];
    for (let i = 0; i < this.n - 1; i++) {
      this.segHeading.push(Math.atan2(this.y[i + 1] - this.y[i], this.x[i + 1] - this.x[i]));
    }
  }
  positionAt(s) {
    s = Math.max(0, Math.min(this.length, s));
    if (this.n < 2) return { x: this.x[0] || 0, y: this.y[0] || 0, heading: 0 };
    let lo = 0, hi = this.n - 2;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.cumDist[mid] <= s) lo = mid; else hi = mid - 1;
    }
    const idx = lo, idxNext = idx + 1;
    const segStart = this.cumDist[idx];
    const segLen = this.cumDist[idxNext] - segStart;
    const frac = segLen <= 1e-9 ? 0 : (s - segStart) / segLen;
    return {
      x: this.x[idx] + frac * (this.x[idxNext] - this.x[idx]),
      y: this.y[idx] + frac * (this.y[idxNext] - this.y[idx]),
      heading: this.segHeading[idx]
    };
  }
}

// ---------------- Closed path lookup ----------------
class ClosedPath {
  constructor(pts) {
    this.x = pts.map(p => p.x);
    this.y = pts.map(p => p.y);
    this.n = this.x.length;
    const segLen = [];
    for (let i = 0; i < this.n; i++) {
      const j = (i + 1) % this.n;
      segLen.push(Math.hypot(this.x[j] - this.x[i], this.y[j] - this.y[i]));
    }
    this.cumDist = [0];
    for (let i = 0; i < this.n; i++) this.cumDist.push(this.cumDist[i] + segLen[i]);
    this.length = this.cumDist[this.n];
    this.cumDist = this.cumDist.slice(0, this.n);
    this.segHeading = [];
    for (let i = 0; i < this.n; i++) {
      const j = (i + 1) % this.n;
      this.segHeading.push(Math.atan2(this.y[j] - this.y[i], this.x[j] - this.x[i]));
    }
  }
  positionAt(s) {
    s = ((s % this.length) + this.length) % this.length;
    let lo = 0, hi = this.n - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (this.cumDist[mid] <= s) lo = mid; else hi = mid - 1;
    }
    const idx = lo;
    const idxNext = (idx + 1) % this.n;
    const segStart = this.cumDist[idx];
    const segLen = (idxNext === 0) ? (this.length - segStart) : (this.cumDist[idxNext] - segStart);
    const frac = segLen <= 1e-9 ? 0 : (s - segStart) / segLen;
    return {
      x: this.x[idx] + frac * (this.x[idxNext] - this.x[idx]),
      y: this.y[idx] + frac * (this.y[idxNext] - this.y[idx]),
      heading: this.segHeading[idx]
    };
  }
}

// ============================================================
// Vehicle
// Extra fields beyond position/speed exist so that admin / car
// messages can steer real behaviour:
//   speedBias   -> persistent m/s offset added by increase/decrease commands
//   stopped     -> true forces the vehicle to a halt (stop command)
//   direction   -> 1 = forward, -1 = reversed (reverse-direction command)
// ============================================================
class Vehicle {
  constructor(id, s0, isLeader) {
    this.id = id; this.isLeader = isLeader; this.s = s0; this.v = 0; this.a = 0;
    this.distanceTraveled = 0; this.x = 0; this.y = 0; this.heading = 0;
    this.prev = null; this.next = null;

    // ---- state that messages can change ----
    this.speedMultiplier = 1;  // 1 = normal, 2 = "double the speed", 0.5 = "half the speed", etc.
    this.stopped = false;      // forced halt
    this.direction = 1;        // 1 forward, -1 reversed

    // ---- per-vehicle noise seed for realistic, non-constant speed ----
    this.noiseSeed = (Math.random() - 0.5) * 2;
  }
  broadcast() { return { id: this.id, s: this.s, v: this.v }; } // <-- V2V message point
}

// ============================================================
// Platoon = linked list + car-following control
// ============================================================
class Platoon {
  constructor(path, nVehicles, spacingM, targetSpeed, closed) {
    this.path = path; this.n = nVehicles; this.targetSpeed = targetSpeed; this.time = 0;
    this.closed = !!closed;
    this.kP = 0.35; this.kV = 0.9; this.standstillGap = 6.0; this.timeGap = 1.0;
    this.aMax = 2.0; this.aMin = -3.0; this.leaderAccel = 1.0;

    // Real-time-feel: small random jitter added to speed every step so the
    // graphs and on-map speeds are never perfectly flat, like real traffic.
    this.noiseAmplitude = Math.max(0.4, targetSpeed * 0.12); // m/s

    this.vehicles = [];
    for (let i = 0; i < nVehicles; i++) {
      let s0;
      if (this.closed) {
        s0 = ((-i * spacingM) % path.length + path.length) % path.length;
      } else {
        s0 = 0;
      }
      this.vehicles.push(new Vehicle(i, s0, i === 0));
    }
    for (let i = 1; i < nVehicles; i++) { this.vehicles[i].prev = this.vehicles[i - 1]; this.vehicles[i - 1].next = this.vehicles[i]; }
    this.updateXY();
  }

  updateXY() { for (const v of this.vehicles) { const p = this.path.positionAt(v.s); v.x = p.x; v.y = p.y; v.heading = p.heading; } }

  allSettled() {
    if (this.closed) return false;
    const leader = this.vehicles[0];
    if (leader.direction >= 0 && leader.s < this.path.length - 1e-6) return false;
    return this.vehicles.every(v => v.v < 0.05);
  }

  // Small random-walk noise (kept in [-1,1] then scaled) so speed wobbles
  // naturally instead of jumping around unrealistically.
  _noise(v) {
    v.noiseSeed += (Math.random() - 0.5) * 0.6;
    v.noiseSeed = Math.max(-1, Math.min(1, v.noiseSeed));
    return v.noiseSeed * this.noiseAmplitude;
  }

  _advance(v, dt) {
    if (v.direction >= 0) {
      if (this.closed) v.s = ((v.s + v.v * dt) % this.path.length + this.path.length) % this.path.length;
      else {
        v.s = Math.min(this.path.length, v.s + v.v * dt);
        if (v.s >= this.path.length) v.v = 0;
      }
    } else {
      if (this.closed) v.s = ((v.s - v.v * dt) % this.path.length + this.path.length) % this.path.length;
      else {
        v.s = Math.max(0, v.s - v.v * dt);
        if (v.s <= 0) v.v = 0;
      }
    }
  }

  step(dt) {
    const leader = this.vehicles[0];

    if (leader.stopped) {
      leader.a = -2.5; leader.v = 0;
    } else {
      const leaderArrived = !this.closed && leader.direction >= 0 && leader.s >= this.path.length - 1e-9;
      if (leaderArrived) {
        leader.v = 0; leader.a = 0;
      } else {
        const desired = Math.max(0, this.targetSpeed * leader.speedMultiplier);
        leader.a = Math.max(this.aMin, Math.min(this.aMax, 1.1 * (desired - leader.v)));
        leader.v = leader.v + leader.a * dt + this._noise(leader) * dt;
        leader.v = Math.max(0, Math.min(leader.v, desired * 1.2 + 2));
      }
    }
    this._advance(leader, dt);
    leader.distanceTraveled += leader.v * dt;

    for (let i = 1; i < this.vehicles.length; i++) {
      const v = this.vehicles[i];

      if (v.stopped) {
        v.a = -2.5; v.v = 0;
      } else {
        const arrived = !this.closed && v.direction >= 0 && v.s >= this.path.length - 1e-9;
        if (arrived) {
          v.v = 0; v.a = 0;
        } else {
          const msg = v.prev.broadcast(); // <-- V2V message received from vehicle ahead
          const commanded = Math.abs(v.speedMultiplier - 1) > 1e-6;

          let a;
          if (commanded) {
            // This vehicle was individually told to change its own speed
            // (increase / decrease / double / half). Track that target
            // directly so the change is clearly visible on just this car.
            const desired = Math.max(0, this.targetSpeed * v.speedMultiplier);
            a = 1.1 * (desired - v.v);
          } else {
            // Normal platooning behaviour: keep safe gap + match vehicle ahead.
            const gap = this.closed
              ? (((msg.s - v.s) % this.path.length) + this.path.length) % this.path.length
              : (msg.s - v.s);
            const desiredGap = this.standstillGap + this.timeGap * v.v;
            const spacingError = gap - desiredGap;
            const speedError = msg.v - v.v;
            a = this.kP * spacingError + this.kV * speedError;
          }
          a = Math.max(this.aMin, Math.min(this.aMax, a));
          v.a = a;
          let newV = v.v + a * dt + this._noise(v) * dt * 0.5;
          const cap = commanded ? (this.targetSpeed * v.speedMultiplier * 1.2 + 2) : (this.targetSpeed * 1.6);
          v.v = Math.max(0, Math.min(cap, newV));
        }
      }
      this._advance(v, dt);
      v.distanceTraveled += v.v * dt;
    }
    this.updateXY();
    this.time += dt;
  }
}