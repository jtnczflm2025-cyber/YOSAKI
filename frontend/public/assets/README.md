# YOSAKI 银灰界面资源

- `silver-atmosphere.png`：本轮按用户视觉方向生成的银灰雾光与透明曲面背景，1672×941。只用于欢迎、工作台及登录氛围，不放在长正文下面。
- `globe-fallback.png`：本轮生成的透明底静态地球，1254×1254。用于动态模块加载及 WebGL／点阵资源失败降级。
- `land-points.json`：从 Natural Earth 1:110m 陆地边界离线采样得到的 12,125 个经纬度点。它们是陆地分布，不是服务节点或私人数据。
- `THIRD_PARTY_LICENSES.txt`：所采用 Three.js 与 three-globe 的许可证文本。

地理数据来源：[Natural Earth land GeoJSON](https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_110m_land.geojson)，[公共领域使用说明](https://www.naturalearthdata.com/about/terms-of-use/)。客户端不请求这些远程地址。

球体由 `frontend/src/ui/globeScene.ts` 绘制；使用 [Three.js](https://github.com/mrdoob/three.js)、[three-globe](https://github.com/vasturiano/three-globe) 和官方 [OrbitControls](https://threejs.org/docs/pages/OrbitControls.html)。构建锁定版本见根目录 package-lock.json。

玻璃位移数学来自用户提供的 Aceternity Liquid Glass 参考代码，位于 `frontend/src/ui/glassRefraction.ts`；Vue 生命周期、分层底面、降级和共享样式为本项目适配。没有引入 React／Tailwind，也未把附带的安装提示当作执行授权。原参考：[Aceternity Liquid Glass](https://ui.aceternity.com/components/liquid-glass)。

未购买付费模型，未使用参考图中的 CYBERMIND 品牌、第三方应用标志或统计数据。
