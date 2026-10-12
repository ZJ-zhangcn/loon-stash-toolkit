/*
 * 盒马开屏广告清理 for Loon
 * 仅处理 mtop.wdk.render.querysinglepage 返回的 pageType=70 / poweron_ad 数据。
 */

let body = $response.body;

try {
  let obj = JSON.parse(body);

  const data = obj && obj.data;
  const scenes = Array.isArray(data && data.scenes) ? data.scenes : [];
  const isSplash =
    obj &&
    obj.api === "mtop.wdk.render.querysinglepage" &&
    data &&
    String(data.pageType) === "70" &&
    (
      String(data.pageName || "").includes("开机屏") ||
      scenes.some(scene => scene && scene.bizCode === "poweron_ad")
    );

  if (isSplash) {
    data.scenes = [];
    data.hasMore = "false";
    data.defaultFirstFeeds = data.defaultFirstFeeds || {};
    data.defaultFirstFeeds.hasMore = "false";
    console.log("Freshippo splash ad removed");
  }

  $done({ body: JSON.stringify(obj) });
} catch (e) {
  console.log("Freshippo splash script error: " + e);
  $done({});
}
