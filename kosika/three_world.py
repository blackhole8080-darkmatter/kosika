"""Self-contained browser scene for the original low-poly AI world preview."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .city import City
from .metropolis import _department_map, _domain_for

THREE_VERSION = "0.180.0"

_SCENE_SCRIPT = r"""
(() => {
  const host = document.getElementById("three-world");
  const fallback = document.getElementById("svg-fallback");
  const data = JSON.parse(document.getElementById("scene-data").textContent);
  if (!host) return;

  const importThree = `https://cdn.jsdelivr.net/npm/three@__VERSION__/build/three.module.js`;
  const importLoader = `https://cdn.jsdelivr.net/npm/three@__VERSION__/examples/jsm/loaders/GLTFLoader.js`;

  const makeTexture = (THREE, colour, accent) => {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = colour;
    ctx.fillRect(0, 0, 64, 64);
    ctx.fillStyle = accent;
    ctx.globalAlpha = 0.22;
    for (let i = -64; i < 128; i += 16) ctx.fillRect(i, 0, 7, 64);
    ctx.globalAlpha = 0.35;
    ctx.fillRect(0, 50, 64, 5);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.wrapS = THREE.RepeatWrapping;
    texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(1.5, 1.5);
    return texture;
  };

  const run = async () => {
    const THREE = await import(importThree);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x08141a);
    scene.fog = new THREE.Fog(0x08141a, 52, 94);

    const width = 2128;
    const height = 1224;
    const aspect = width / height;
    const camera = new THREE.OrthographicCamera(
      -25 * aspect, 25 * aspect, 25, -25, 0.1, 180
    );
    camera.position.set(38, 38, 38);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
    renderer.setSize(width, height, false);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);

    const hemi = new THREE.HemisphereLight(0xCBE7FF, 0x24422E, 2.1);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xFFF0C7, 3.2);
    sun.position.set(-20, 38, 18);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = -48;
    sun.shadow.camera.right = 48;
    sun.shadow.camera.top = 48;
    sun.shadow.camera.bottom = -48;
    scene.add(sun);

    const groundMat = new THREE.MeshStandardMaterial({
      map: makeTexture(THREE, "#315D3B", "#75A85B"),
      roughness: 1,
    });
    const pathMat = new THREE.MeshStandardMaterial({
      map: makeTexture(THREE, "#9D8061", "#E2C495"),
      roughness: 0.95,
    });
    const waterMat = new THREE.MeshStandardMaterial({
      color: 0x28AFC1,
      roughness: 0.25,
      metalness: 0.05,
      transparent: true,
      opacity: 0.9,
    });

    const ground = new THREE.Mesh(new THREE.BoxGeometry(44, 1.2, 34), groundMat);
    ground.position.y = -0.8;
    ground.receiveShadow = true;
    scene.add(ground);

    const pathGeometry = new THREE.BoxGeometry(1.02, 0.12, 1.02);
    for (let x = -16; x <= 16; x += 1) {
      for (const z of [-0.5, 0.5]) {
        const tile = new THREE.Mesh(pathGeometry, pathMat);
        tile.position.set(x, 0.03, z);
        tile.receiveShadow = true;
        scene.add(tile);
      }
    }
    for (let z = -12; z <= 12; z += 1) {
      for (const x of [-0.5, 0.5]) {
        const tile = new THREE.Mesh(pathGeometry, pathMat);
        tile.position.set(x, 0.035, z);
        tile.receiveShadow = true;
        scene.add(tile);
      }
    }

    for (let x = 10; x <= 16; x += 1) {
      for (let z = 8; z <= 12; z += 1) {
        const tile = new THREE.Mesh(new THREE.BoxGeometry(1.02, 0.08, 1.02), waterMat);
        tile.position.set(x, 0.08, z);
        tile.receiveShadow = true;
        scene.add(tile);
      }
    }

    const makeTree = (x, z, scale = 1) => {
      const tree = new THREE.Group();
      tree.position.set(x, 0, z);
      const trunk = new THREE.Mesh(
        new THREE.CylinderGeometry(0.25 * scale, 0.38 * scale, 2.3 * scale, 6),
        new THREE.MeshStandardMaterial({ color: 0x70462B, roughness: 1 })
      );
      trunk.position.y = 1.15 * scale;
      trunk.castShadow = true;
      tree.add(trunk);
      const lower = new THREE.Mesh(
        new THREE.ConeGeometry(1.65 * scale, 2.5 * scale, 6),
        new THREE.MeshStandardMaterial({ color: 0x3E9B58, roughness: 1 })
      );
      lower.position.y = 3.0 * scale;
      lower.castShadow = true;
      tree.add(lower);
      const upper = new THREE.Mesh(
        new THREE.ConeGeometry(1.1 * scale, 2.0 * scale, 6),
        new THREE.MeshStandardMaterial({ color: 0x70C56F, roughness: 1 })
      );
      upper.position.y = 4.7 * scale;
      upper.castShadow = true;
      tree.add(upper);
      scene.add(tree);
    };
    data.trees.forEach((tree) => makeTree(tree.x, tree.z, tree.scale));

    const buildingGroups = new Map();
    const agentGroups = new Map();
    const palette = [0xD66B4D, 0xD7A83E, 0x7C86D9, 0xB768C5, 0x51A7A0, 0xD48B52];

    const addWindow = (group, x, y, z, material) => {
      const window = new THREE.Mesh(new THREE.BoxGeometry(0.8, 1.0, 0.08), material);
      window.position.set(x, y, z);
      window.castShadow = true;
      group.add(window);
    };

    const makeBuilding = (building, index) => {
      const group = new THREE.Group();
      group.position.set(building.x, 0, building.z);
      group.userData = { building, kind: "building" };
      const colour = palette[index % palette.length];
      const wallMat = new THREE.MeshStandardMaterial({
        map: makeTexture(THREE, `#${colour.toString(16).padStart(6, "0")}`, "#FFF1B6"),
        roughness: 0.82,
      });
      const roofMat = new THREE.MeshStandardMaterial({ color: 0x4C6F9B, roughness: 0.7 });
      const trimMat = new THREE.MeshStandardMaterial({ color: 0xF4C66A, roughness: 0.65 });
      const glassMat = new THREE.MeshStandardMaterial({
        color: 0xB9F0E6,
        emissive: 0x17483F,
        emissiveIntensity: 0.55,
        roughness: 0.25,
      });
      const wall = new THREE.Mesh(new THREE.BoxGeometry(6.2, 4.6, 4.5), wallMat);
      wall.position.y = 2.35;
      wall.castShadow = true;
      wall.receiveShadow = true;
      group.add(wall);
      const roof = new THREE.Mesh(new THREE.ConeGeometry(4.9, 2.9, 4), roofMat);
      roof.rotation.y = Math.PI / 4;
      roof.position.y = 6.1;
      roof.castShadow = true;
      group.add(roof);
      const tower = new THREE.Mesh(new THREE.BoxGeometry(1.55, 5.2, 1.55), trimMat);
      tower.position.set(-2.0, 5.1, -1.2);
      tower.castShadow = true;
      group.add(tower);
      const towerRoof = new THREE.Mesh(new THREE.ConeGeometry(1.35, 2.5, 4), trimMat);
      towerRoof.rotation.y = Math.PI / 4;
      towerRoof.position.set(-2.0, 9.0, -1.2);
      towerRoof.castShadow = true;
      group.add(towerRoof);
      const door = new THREE.Mesh(new THREE.BoxGeometry(1.15, 2.0, 0.15), new THREE.MeshStandardMaterial({ color: 0x392A2C }));
      door.position.set(0, 1.0, 2.28);
      door.castShadow = true;
      group.add(door);
      addWindow(group, -1.9, 2.6, 2.28, glassMat);
      addWindow(group, 0, 2.6, 2.28, glassMat);
      addWindow(group, 1.9, 2.6, 2.28, glassMat);
      const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.045, 3.0, 6), trimMat);
      pole.position.set(-2.0, 11.1, -1.2);
      group.add(pole);
      const flag = new THREE.Mesh(new THREE.PlaneGeometry(1.2, 0.65), new THREE.MeshStandardMaterial({ color: colour, side: THREE.DoubleSide }));
      flag.position.set(-1.45, 11.6, -1.2);
      flag.rotation.y = Math.PI / 2;
      group.add(flag);
      scene.add(group);
      buildingGroups.set(building.id || building.domain, group);
    };
    data.buildings.forEach(makeBuilding);

    const makeAgent = (agent, index) => {
      const group = new THREE.Group();
      group.position.set(agent.x, 0, agent.z);
      group.userData = { agent, activeUntil: 0, pending: false, baseY: 0 };
      const colour = palette[(index + 2) % palette.length];
      const body = new THREE.Mesh(
        new THREE.CylinderGeometry(0.32, 0.44, 0.95, 6),
        new THREE.MeshStandardMaterial({ color: colour, roughness: 0.8, emissive: 0x000000 })
      );
      body.position.y = 0.7;
      body.castShadow = true;
      group.add(body);
      const head = new THREE.Mesh(
        new THREE.SphereGeometry(0.34, 8, 6),
        new THREE.MeshStandardMaterial({ color: 0xF4D9BE, roughness: 0.9 })
      );
      head.position.y = 1.48;
      head.castShadow = true;
      group.add(head);
      const marker = new THREE.Mesh(
        new THREE.RingGeometry(0.5, 0.62, 12),
        new THREE.MeshBasicMaterial({ color: 0x4ADE80, side: THREE.DoubleSide, transparent: true, opacity: 0.0 })
      );
      marker.rotation.x = -Math.PI / 2;
      marker.position.y = 0.08;
      group.add(marker);
      scene.add(group);
      agentGroups.set(agent.name, { group, body, marker });
    };
    data.agents.forEach(makeAgent);

    const makeRock = (x, z, scale) => {
      const rock = new THREE.Mesh(
        new THREE.DodecahedronGeometry(scale, 0),
        new THREE.MeshStandardMaterial({ color: 0x8797A0, roughness: 1 })
      );
      rock.position.set(x, scale * 0.7, z);
      rock.scale.y = 0.65;
      rock.castShadow = true;
      scene.add(rock);
    };
    data.rocks.forEach((rock) => makeRock(rock.x, rock.z, rock.scale));

    const fountain = new THREE.Group();
    const fountainBase = new THREE.Mesh(new THREE.CylinderGeometry(1.7, 1.9, 0.55, 8), new THREE.MeshStandardMaterial({ color: 0x91AAB0, roughness: 0.8 }));
    fountainBase.position.y = 0.3;
    fountainBase.castShadow = true;
    fountain.add(fountainBase);
    const fountainWater = new THREE.Mesh(new THREE.CylinderGeometry(1.35, 1.35, 0.08, 8), waterMat);
    fountainWater.position.y = 0.62;
    fountain.add(fountainWater);
    const fountainJet = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.2, 1.4, 8), new THREE.MeshStandardMaterial({ color: 0xA9F2EE, emissive: 0x1B777A, emissiveIntensity: 0.7 }));
    fountainJet.position.y = 1.25;
    fountain.add(fountainJet);
    fountain.position.set(-1.1, 0, -1.1);
    scene.add(fountain);

    const gltfAssets = data.buildings.filter((building) => building.asset);
    if (gltfAssets.length) {
      try {
        const { GLTFLoader } = await import(importLoader);
        const loader = new GLTFLoader();
        gltfAssets.forEach((building) => loader.load(building.asset, (asset) => {
          const target = buildingGroups.get(building.id || building.domain);
          if (!target) return;
          target.add(asset.scene);
        }));
      } catch (error) {
        console.warn("Optional glTF assets unavailable; procedural models remain active.", error);
      }
    }

    window.kosikaThree = {
      wake(name) {
        const item = agentGroups.get(name);
        if (!item) return;
        item.group.userData.activeUntil = performance.now() + 2200;
        item.marker.material.color.set(0x4ADE80);
        item.marker.material.opacity = 0.95;
      },
      gate(name) {
        const item = agentGroups.get(name);
        if (!item) return;
        item.group.userData.activeUntil = performance.now() + 4200;
        item.group.userData.pending = true;
        item.marker.material.color.set(0xF59E0B);
        item.marker.material.opacity = 0.95;
      },
    };

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    renderer.domElement.addEventListener("click", (event) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects([...buildingGroups.values()], true);
      const hit = hits.find((item) => item.object.parent?.userData?.building || item.object.userData?.building);
      if (!hit) return;
      const root = hit.object.parent?.userData?.building ? hit.object.parent : hit.object;
      const building = root.userData.building;
      host.dataset.selectedBuilding = building.label || building.domain;
    });

    fallback.style.display = "none";
    host.classList.add("ready");
    const clock = new THREE.Clock();
    const animate = () => {
      const elapsed = clock.getElapsedTime();
      const now = performance.now();
      agentGroups.forEach(({ group, body, marker }) => {
        const active = group.userData.activeUntil > now;
        body.material.emissive.set(active ? (group.userData.pending ? 0x7A3E00 : 0x145A42) : 0x000000);
        body.material.emissiveIntensity = active ? 1.2 : 0;
        marker.material.opacity = active ? 0.95 : 0.0;
        if (active) group.position.y = Math.sin(elapsed * 8 + group.position.x) * 0.08;
        else group.position.y = 0;
        if (!active) group.userData.pending = false;
      });
      fountain.rotation.y = elapsed * 0.18;
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    };
    animate();
  };

  run().catch((error) => {
    console.warn("Three.js AI world unavailable; retaining the SVG fallback.", error);
    host.dataset.error = String(error);
  });
})();
""".replace("__VERSION__", THREE_VERSION)


def build_scene_data(
    city: City,
    *,
    world_labels: Mapping[str, str] | None = None,
    world_kinds: Mapping[str, str] | None = None,
    world_buildings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build bounded data for the browser's procedural low-poly scene."""
    labels = world_labels or {}
    kinds = world_kinds or {}
    building_ids = world_buildings or {}
    departments = _department_map(city)
    domains: dict[str, list[str]] = {}
    for person in city.residents:
        domain = _domain_for(person, departments.get(person.name, ""))
        domains.setdefault(domain, []).append(person.name)
    positions = ((-11, -8), (0, -10), (11, -5), (-8, 4), (2, 6), (11, 8), (-13, 8))
    buildings: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    for index, (domain, people) in enumerate(domains.items()):
        x, z = positions[index % len(positions)]
        building_id = next(
            (building_ids[name] for name in people if name in building_ids),
            f"domain-{index}",
        )
        buildings.append(
            {
                "id": building_id,
                "domain": domain,
                "label": labels.get(domain, domain),
                "kind": kinds.get(domain, "institution"),
                "x": x,
                "z": z,
                "asset": None,
            }
        )
        for person_index, name in enumerate(people):
            agents.append(
                {
                    "name": name,
                    "domain": domain,
                    "building": building_id,
                    "x": x - 2.0 + (person_index % 3) * 1.8,
                    "z": z + 4.3 + (person_index // 3) * 1.4,
                }
            )
    return {
        "buildings": buildings,
        "agents": agents,
        "trees": [
            {"x": -16, "z": -10, "scale": 1.1},
            {"x": -13, "z": -2, "scale": 0.9},
            {"x": -15, "z": 9, "scale": 1.2},
            {"x": -4, "z": -12, "scale": 0.8},
            {"x": 8, "z": -13, "scale": 1.0},
            {"x": 16, "z": -10, "scale": 1.15},
            {"x": 16, "z": 2, "scale": 0.95},
            {"x": 17, "z": 13, "scale": 1.2},
            {"x": 4, "z": 14, "scale": 0.9},
        ],
        "rocks": [
            {"x": -8, "z": -1, "scale": 0.7},
            {"x": 7, "z": 3, "scale": 0.85},
            {"x": -14, "z": 14, "scale": 0.6},
        ],
    }


def render_three_world(scene_data: dict[str, Any]) -> str:
    """Return the canvas host and module script for the live low-poly scene."""
    payload = json.dumps(scene_data, sort_keys=True, ensure_ascii=True).replace(
        "<", "\\u003c"
    )
    return (
        '<div id="three-world" aria-label="Interactive low-poly Kosika AI world"></div>'
        f'<script type="application/json" id="scene-data">{payload}</script>'
        f'<script type="module">{_SCENE_SCRIPT}</script>'
    )


__all__ = ["build_scene_data", "render_three_world"]
