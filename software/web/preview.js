/* PalmRobot 3D 预览组件（three.js）
 *
 * 上位机 WebView2 与浏览器演示页共用同一份代码。
 * 数据源：window.SESAME_ASSETS（build_assets.py 生成，file:// 可用）
 *        若不存在则回退 fetch ./assets/mjcf/sesame.xml 与 ./assets/meshes/*.stl
 *
 * 公开 API（挂 window.PalmRobotPreview）：
 *   init(container)                 初始化渲染
 *   setServoAngles([8])             固件通道角 0-180°，ch 序 R1,R2,L1,L2,R4,R3,L3,L4
 *   playMotion(nameOrJson)          播放 .motion（内嵌名或 JSON 字符串/对象）
 *   stopMotion() / resetPose()
 *   onStatus = fn(text)             组件向宿主回调消息（可选）
 */
(function () {
  'use strict';

  // —— 常量（与 docs/20 §5.2、assets/calibration/default.yaml 一致）——
  var JOINT_ORDER = ['R1', 'R2', 'L1', 'L2', 'R4', 'R3', 'L3', 'L4'];
  var STAND_DEG = [135, 45, 45, 135, 0, 180, 0, 180];

  var scene, camera, renderer, controls, container;
  var jointMap = {};      // 关节名 -> {group, ref, rangeMin, rangeMax, axis}
  var currentAngles = STAND_DEG.slice();
  var motion = null;      // {name, duration_ms, loop, frames:[{t, ch[]}]}
  var motionStart = 0;
  var rafId = 0;
  var ready = false;

  // ---------- 资产加载 ----------

  function getAssets() {
    if (window.SESAME_ASSETS) return Promise.resolve(window.SESAME_ASSETS);
    // HTTP 模式回退
    return fetch('./assets/mjcf/sesame.xml')
      .then(function (r) { return r.text(); })
      .then(function (xml) {
        return { mjcf: xml, meshes: null, motions: {} };   // meshes 由 STLLoader 按 URL 加载
      });
  }

  function base64ToArrayBuffer(b64) {
    var bin = atob(b64);
    var len = bin.length;
    var bytes = new Uint8Array(len);
    for (var i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  // ---------- MJCF 解析 ----------

  function parseMjcf(xmlText) {
    var doc = new DOMParser().parseFromString(xmlText, 'text/xml');
    var err = doc.querySelector('parsererror');
    if (err) throw new Error('MJCF 解析失败: ' + err.textContent.slice(0, 200));

    // 材质表
    var materials = {};
    doc.querySelectorAll('asset > material').forEach(function (m) {
      materials[m.getAttribute('name')] = m.getAttribute('rgba');
    });
    // mesh 表：name -> {file, scale}
    var meshes = {};
    doc.querySelectorAll('asset > mesh').forEach(function (m) {
      meshes[m.getAttribute('name')] = {
        file: m.getAttribute('file'),
        scale: parseFloat((m.getAttribute('scale') || '1').split(' ')[0]),
      };
    });

    // default class="servo" 的 axis 约定（本项目固定 0 0 -1）
    var servoAxis = new THREE.Vector3(0, 0, -1);

    // worldbody 下找 name="base" 的 body（本项目根）
    var baseEl = null;
    doc.querySelectorAll('worldbody > body').forEach(function (b) {
      if (b.getAttribute('name') === 'base') baseEl = b;
    });
    if (!baseEl) throw new Error('MJCF 缺少 body[name=base]');

    return { baseEl: baseEl, materials: materials, meshes: meshes, servoAxis: servoAxis };
  }

  function parseVec(s, def) {
    if (!s) return def ? def.clone() : new THREE.Vector3();
    var p = s.trim().split(/\s+/).map(parseFloat);
    return new THREE.Vector3(p[0], p[1], p[2]);
  }

  function parseQuat(s) {
    // MuJoCo "w x y z" → three (x, y, z, w)
    if (!s) return new THREE.Quaternion();
    var p = s.trim().split(/\s+/).map(parseFloat);
    return new THREE.Quaternion(p[1], p[2], p[3], p[0]);
  }

  // ---------- 层级构建 ----------

  function buildBody(el, parsed, meshMap, parentGroup) {
    var group = new THREE.Group();
    group.name = el.getAttribute('name') || 'body';
    group.position.copy(parseVec(el.getAttribute('pos'), new THREE.Vector3()));
    var bodyQuat = parseQuat(el.getAttribute('quat'));
    group.quaternion.copy(bodyQuat);

    Array.prototype.slice.call(el.children).forEach(function (child) {
      var tag = child.tagName;
      if (tag === 'geom') {
        var cls = child.getAttribute('class');
        if (cls !== 'visual') return;                    // 只渲染视觉几何
        var meshRef = child.getAttribute('mesh');
        var info = meshRef ? parsed.meshes[meshRef] : null;
        if (!info || !meshMap[info.file]) return;
        var geo = meshMap[info.file].clone();
        var mat = makeMaterial(child.getAttribute('material'), parsed.materials);
        var mesh = new THREE.Mesh(geo, mat);
        mesh.position.copy(parseVec(child.getAttribute('pos'), new THREE.Vector3()));
        mesh.quaternion.copy(parseQuat(child.getAttribute('quat')));
        mesh.scale.setScalar(info.scale);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        group.add(mesh);
      } else if (tag === 'joint') {
        // 记录关节（本项目 class="servo" hinge，axis 0 0 -1）
        var range = (child.getAttribute('range') || '0 0').trim().split(/\s+/).map(parseFloat);
        jointMap[child.getAttribute('name')] = {
          group: group,
          bodyQuat: bodyQuat.clone(),
          ref: parseFloat(child.getAttribute('ref') || '0'),
          rangeMin: range[0],
          rangeMax: range[1],
          axis: parsed.servoAxis,
        };
      } else if (tag === 'body') {
        buildBody(child, parsed, meshMap, group);
      }
    });

    parentGroup.add(group);
    return group;
  }

  function makeMaterial(name, materials) {
    var rgba = materials[name] || '0.9 0.4 0.1 1';
    var p = rgba.trim().split(/\s+/).map(parseFloat);
    var color = new THREE.Color(p[0], p[1], p[2]);
    return new THREE.MeshStandardMaterial({
      color: color,
      roughness: 0.72,
      metalness: 0.05,
      transparent: p[3] < 1,
      opacity: p[3],
    });
  }

  // ---------- 场景 ----------

  function initScene(box) {
    container = box;
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b1220);
    scene.fog = new THREE.Fog(0x0b1220, 0.8, 2.4);

    camera = new THREE.PerspectiveCamera(42, 1, 0.01, 10);
    camera.up.set(0, 0, 1);                              // MuJoCo Z-up
    camera.position.set(0.32, -0.30, 0.22);
    camera.lookAt(0, 0, 0.05);

    renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputEncoding = THREE.sRGBEncoding;
    box.appendChild(renderer.domElement);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0, 0.05);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxDistance = 1.2;
    controls.minDistance = 0.12;

    // 灯光
    var hemi = new THREE.HemisphereLight(0x8ab6ff, 0x141a26, 0.55);
    scene.add(hemi);
    var key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(-0.5, -0.6, 0.9);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.camera.left = -0.3; key.shadow.camera.right = 0.3;
    key.shadow.camera.top = 0.3; key.shadow.camera.bottom = -0.3;
    scene.add(key);
    var fill = new THREE.DirectionalLight(0x22d3ee, 0.25);
    fill.position.set(0.5, 0.4, 0.3);
    scene.add(fill);

    // 地面
    var floor = new THREE.Mesh(
      new THREE.CircleGeometry(0.55, 48),
      new THREE.MeshStandardMaterial({ color: 0x111a2c, roughness: 0.95 })
    );
    floor.receiveShadow = true;
    scene.add(floor);
    var grid = new THREE.GridHelper(1.1, 44, 0x1c3a52, 0x14243a);
    grid.rotation.x = Math.PI / 2;
    scene.add(grid);

    onResize();
    window.addEventListener('resize', onResize);
    animate();
  }

  function onResize() {
    if (!container) return;
    var w = container.clientWidth || 640;
    var h = container.clientHeight || 480;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }

  function animate() {
    rafId = requestAnimationFrame(animate);
    if (motion) tickMotion();
    controls.update();
    renderer.render(scene, camera);
  }

  // ---------- 运动学应用 ----------

  function applyAngles(deg) {
    currentAngles = deg.slice(0, 8);
    for (var i = 0; i < 8; i++) {
      var j = jointMap[JOINT_ORDER[i]];
      if (!j) continue;
      // 固件角度域（0-180°）即 MJCF ctrl/qpos 域；关节相对建模姿态的转角 = rad(deg) - ref
      var target = THREE.MathUtils.clamp(THREE.MathUtils.degToRad(deg[i]), j.rangeMin, j.rangeMax);
      var angle = target - j.ref;
      var q = new THREE.Quaternion().setFromAxisAngle(j.axis, angle);
      j.group.quaternion.copy(j.bodyQuat).multiply(q);
    }
  }

  // ---------- .motion 播放 ----------

  function playMotion(input) {
    var m = null;
    if (typeof input === 'string') {
      var assets = window.SESAME_ASSETS || { motions: {} };
      if (assets.motions[input]) m = JSON.parse(assets.motions[input]);
      else { try { m = JSON.parse(input); } catch (e) { /* 不是 JSON */ } }
    } else if (input && typeof input === 'object') {
      m = input;
    }
    if (!m || !m.tracks || !m.tracks.servo || !m.tracks.servo.length) {
      report('播放失败：无效动作数据');
      return false;
    }
    motion = {
      name: m.name || 'unnamed',
      duration: Math.max(1, m.duration_ms || 1000),
      loop: !!m.loop,
      frames: m.tracks.servo.map(function (f) { return { t: f.t, ch: f.ch.slice(0, 8) }; }),
    };
    motionStart = performance.now();
    report('播放动作: ' + motion.name + (motion.loop ? '（循环）' : ''));
    return true;
  }

  function tickMotion() {
    var elapsed = (performance.now() - motionStart) % (motion.loop ? motion.duration : Infinity);
    if (!motion.loop && elapsed >= motion.duration) {
      stopMotion(true);
      return;
    }
    var t = motion.loop ? elapsed % motion.duration : elapsed;
    var frames = motion.frames;
    var i = 0;
    while (i < frames.length - 1 && frames[i + 1].t <= t) i++;
    var f0 = frames[i], f1 = frames[Math.min(i + 1, frames.length - 1)];
    var span = Math.max(1, f1.t - f0.t);
    var k = THREE.MathUtils.clamp((t - f0.t) / span, 0, 1);
    var out = new Array(8);
    for (var c = 0; c < 8; c++) out[c] = f0.ch[c] + (f1.ch[c] - f0.ch[c]) * k;
    applyAngles(out);
  }

  function stopMotion(hold) {
    var was = motion ? motion.name : null;
    motion = null;
    if (was) report(hold ? '动作结束: ' + was : '停止播放');
  }

  function report(text) {
    if (typeof window.PalmRobotPreview.onStatus === 'function') window.PalmRobotPreview.onStatus(text);
    if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage)
      window.chrome.webview.postMessage({ type: 'preview', text: text });
  }

  // ---------- 启动 ----------

  function boot(box) {
    return getAssets()
      .then(function (assets) {
        initScene(box);
        var parsed = parseMjcf(assets.mjcf);

        var meshMap = {};
        var loader = new THREE.STLLoader();
        if (assets.meshes) {
          Object.keys(assets.meshes).forEach(function (file) {
            meshMap[file] = loader.parse(base64ToArrayBuffer(assets.meshes[file]));
          });
        } else {
          // HTTP 模式：按文件名 URL 加载（省略，P0 内嵌模式为主）
        }

        var root = new THREE.Group();
        scene.add(root);
        buildBody(parsed.baseEl, parsed, meshMap, root);

        applyAngles(STAND_DEG);
        ready = true;
        report('3D 预览就绪（' + Object.keys(jointMap).length + ' 关节）');
        window.PalmRobotPreview.standPose = STAND_DEG.slice();
        return true;
      })
      .catch(function (e) {
        report('初始化失败: ' + e.message);
        throw e;
      });
  }

  window.PalmRobotPreview = {
    init: boot,
    setServoAngles: function (deg) {
      if (!ready || !deg || deg.length !== 8) return false;
      stopMotion(true);
      applyAngles(deg);
      return true;
    },
    playMotion: playMotion,
    stopMotion: function () { stopMotion(false); },
    resetPose: function () {
      stopMotion(false);
      applyAngles(STAND_DEG);
    },
    isReady: function () { return ready; },
    onStatus: null,
  };
})();
