let body = $response.body || "";

try {
  let decodedBody = body;
  let shouldEncode = false;

  if (/^%7B/i.test(body) || body.includes("%22MarketingPage%22")) {
    decodedBody = decodeURIComponent(body);
    shouldEncode = true;
  }

  const config = JSON.parse(decodedBody);

  if (config.MarketingPage) {
    config.MarketingPage.Enable = false;
    config.MarketingPage.ImageInfo = [];
    config.MarketingPage.DisplayStartTime = "";
    config.MarketingPage.DisplayEndTime = "";
  }

  if (config.MarketingVideo) {
    config.MarketingVideo.Enable = false;
  }

  if (config.RcmCommonLog && Array.isArray(config.RcmCommonLog.MarketingPage)) {
    config.RcmCommonLog.MarketingPage = [];
  }

  const nextBody = JSON.stringify(config);
  $done({ body: shouldEncode ? encodeURIComponent(nextBody) : nextBody });
} catch (error) {
  $done({});
}
