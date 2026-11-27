import { once } from "node:events";
import { createServer } from "node:http";

const ns = {
  device: "http://www.onvif.org/ver10/device/wsdl", events: "http://www.onvif.org/ver10/events/wsdl",
  topics: "http://www.onvif.org/ver10/topics", schema: "http://www.onvif.org/ver10/schema",
  wsa: "http://www.w3.org/2005/08/addressing", wsn: "http://docs.oasis-open.org/wsn/b-2",
  top: "http://docs.oasis-open.org/wsn/t-1", soap: "http://www.w3.org/2003/05/soap-envelope",
  ref: "urn:proxigo:fixture:subscription",
};

export async function onvifEventsFixture() {
  let supported = true, available = true, clockOffset = 0;
  let pushes: { id: number; at: string }[] = [];
  let subscriptions = 0, pulls = 0;
  let origin = "";
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString("utf8");
    if (!available) { response.writeHead(503).end(); return; }
    const reply = (payload: string) => response.writeHead(200, { "Content-Type": "application/soap+xml" })
      .end(`<soap:Envelope ${Object.entries(ns).map(([name, url]) => `xmlns:${name}="${url}"`).join(" ")}><soap:Body>${payload}</soap:Body></soap:Envelope>`);
    if (body.includes(":GetServices")) {
      reply(`<device:GetServicesResponse><device:Service><device:Namespace>${ns.events}</device:Namespace>
        <device:XAddr>${origin}/events</device:XAddr></device:Service></device:GetServicesResponse>`);
    } else if (body.includes(":GetSystemDateAndTime")) {
      const date = new Date(Date.now() + clockOffset * 1000);
      reply(`<device:GetSystemDateAndTimeResponse><device:SystemDateAndTime><schema:UTCDateTime>
        <schema:Date><schema:Year>${date.getUTCFullYear()}</schema:Year><schema:Month>${date.getUTCMonth() + 1}</schema:Month>
        <schema:Day>${date.getUTCDate()}</schema:Day></schema:Date><schema:Time><schema:Hour>${date.getUTCHours()}</schema:Hour>
        <schema:Minute>${date.getUTCMinutes()}</schema:Minute><schema:Second>${date.getUTCSeconds()}</schema:Second>
        </schema:Time></schema:UTCDateTime></device:SystemDateAndTime></device:GetSystemDateAndTimeResponse>`);
    } else if (body.includes(":GetEventProperties")) {
      reply(`<events:GetEventPropertiesResponse><top:TopicSet>${supported ? "<topics:RuleEngine><topics:LineDetector><topics:Crossed /></topics:LineDetector></topics:RuleEngine>" : ""}
        </top:TopicSet></events:GetEventPropertiesResponse>`);
    } else if (body.includes(":CreatePullPointSubscription") && body.includes("RuleEngine/LineDetector/Crossed")) {
      subscriptions++;
      reply(`<events:CreatePullPointSubscriptionResponse><events:SubscriptionReference><wsa:Address>${origin}/pull</wsa:Address>
        <wsa:ReferenceParameters><ref:SubscriptionId>${subscriptions}</ref:SubscriptionId></wsa:ReferenceParameters></events:SubscriptionReference>
        <events:CurrentTime>${new Date().toISOString()}</events:CurrentTime>
        <events:TerminationTime>${new Date(Date.now() + 2800).toISOString()}</events:TerminationTime></events:CreatePullPointSubscriptionResponse>`);
    } else if (request.url === "/pull" && body.includes(":PullMessages") && body.includes("IsReferenceParameter")) {
      pulls++;
      const batch = pushes.splice(0, 16).map(({ at, id }) => `<wsn:NotificationMessage><wsn:Topic>topics:RuleEngine/LineDetector/Crossed</wsn:Topic>
        <wsn:Message><schema:Message UtcTime="${at}" PropertyOperation="Changed"><schema:Source>
        <schema:SimpleItem Name="VideoSourceConfigurationToken" Value="fixture-video"/><schema:SimpleItem Name="Rule" Value="fixture-line"/>
        </schema:Source><schema:Data><schema:SimpleItem Name="ObjectId" Value="${id}"/></schema:Data></schema:Message></wsn:Message></wsn:NotificationMessage>`);
      reply(`<events:PullMessagesResponse>${batch.join("")}</events:PullMessagesResponse>`);
    } else {
      response.writeHead(400).end();
    }
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  origin = "http://127.0.0.1:" + (server.address() as { port: number }).port;
  return {
    address: origin + "/device",
    announce(id: number, at = new Date().toISOString()) { pushes.push({ id, at }); return at; },
    configure(options: { supported?: boolean; available?: boolean; clockOffset?: number }) {
      supported = options.supported ?? supported;
      available = options.available ?? available;
      clockOffset = options.clockOffset ?? clockOffset;
    },
    stats: () => ({ subscriptions, pulls }),
    close() { pushes = []; server.closeAllConnections(); server.close(); },
  };
}
