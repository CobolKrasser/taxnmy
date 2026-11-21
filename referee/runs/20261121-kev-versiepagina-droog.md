# Importer, dry run

- rows: 55; pages read: yes
- files proposed: 31 (22 product, 9 publisher)

| outcome | reason | number |
| --- | --- | --- |
| aside | no versions.source | 35 |
| candidate | for the referee | 12 |
| parked | closed source | 5 |
| parked | name not on the product page | 2 |
| parked | source not readable | 1 |

| outcome per product | what | rows | products |
| --- | --- | --- | --- |
| for the referee | no verdict yet | 12 | 12 |
| parked | by the gate or the referee, for another reason than a closed source | 3 | 3 |
| closed source | parked: a source refuses robots | 5 | 5 |
| aside | the find round or the grouping marked it as no product of its own, or without a versions page | 35 | 34 |
| **together** | | **55** | **54** |

**Provisional count, before the verdict of the referee:** 3 of 20 proposed products parked for another reason than a closed source. Nothing is said about going on or stopping: that follows the verdict (`importer.py count`).

Set aside by the find round or the grouping (not counted): 34 products.
Closed, and read on another page of the publisher (TAX-49): 0 products.
Closed, and still closed: 5 products.

**Address it ended at, proposed** (TAX-42): 6 sources
- kev-versiepagina-55.csv:13 dell-technologies/recoverpoint-for-virtual-machines: https://www.dell.com/en-us/dt/data-protection/recoverpoint.htm -> https://www.dell.com/en-us/learn/it-infrastructure/lp/cyber-resilience/data-protection-suite-recoverpoint-for-virtual-machines
- kev-versiepagina-55.csv:18 gogs/gogs: https://gogs.io/ -> https://gogs.io/getting-started/introduction
- kev-versiepagina-55.csv:41 qlik/qlik-sense: https://www.qlik.com/us/products/qlik-sense -> https://www.qlik.com/us/da/products/qlik-sense
- kev-versiepagina-55.csv:41 qlik/qlik-sense: https://help.qlik.com/sense/WhatsNew/What-is-new-QlikSense.htm -> https://help.qlik.com/en-US/sense/November2026/Content/Sense_Helpsites/WhatsNew/What-is-new-QlikSense.htm
- kev-versiepagina-55.csv:43 massimo-melina/hfs-http-file-server: https://github.com/rejetto/hfs/releases/latest -> https://github.com/rejetto/hfs/releases/tag/v3.4.1
- kev-versiepagina-55.csv:47 signalwire/freeswitch: https://www.signalwire.com/freeswitch -> https://signalwire.com/developers/freeswitch

| product | rows | outcome | reason |
| --- | --- | --- | --- |
| adobe/adobe-flash-player | 2 | aside | no versions.source |
| advantive/veracore | 3 | aside | no versions.source |
| ami/megarac-sp-x | 4 | aside | no versions.source |
| anyscale/ray | 42 | for the referee |  |
| aqua-security-software/trivy | 6 | for the referee |  |
| asus/asus-live-update | 7 | aside | no versions.source |
| audinate/dante-discovery | 8 | aside | no versions.source |
| cisco/cisco-broadworks | 9 | for the referee |  |
| d-link/dcs-2670l | 10 | aside | no versions.source |
| d-link/dsl-2750b | 11 | aside | no versions.source |
| dassault-systemes/delmia-apriso | 12 | aside | no versions.source |
| dell-technologies/recoverpoint-for-virtual-machines | 13 | closed source | https://www.dell.com/support/kbdoc/en-us/000376653: refused; the address it ended at, proposed: https://www.dell.com/en-us/dt/data-protection/recoverpoint.htm -> https://www.dell.com/en-us/learn/it-in |
| digiever/ds-2105-pro | 14 | aside | no versions.source |
| edimax-technology/ic-7100 | 15 | aside | no versions.source |
| geosolutions/jai-ext | 36 | aside | no versions.source |
| gladinet/triofox | 16, 17 | aside | no versions.source |
| gogs/gogs | 18 | for the referee |  |
| google/v8 | 19 | for the referee |  |
| google/webrtc | 20 | aside | no versions.source |
| gpustack-ai/gpustack | 21 | for the referee |  |
| hewlett-packard-enterprise/hpe-oneview | 22 | closed source | https://www.hpe.com/nl/en/software/oneview.html: disallowed |
| justice-av-solutions/viewer-8 | 23 | aside | no versions.source |
| laravel/livewire | 24 | for the referee |  |
| massimo-melina/hfs-http-file-server | 43 | parked | name not on the product page |
| microsoft/microsoft-publisher | 27 | aside | no versions.source |
| microsoft/partner-center | 25 | aside | no versions.source |
| microsoft/power-pages | 26 | aside | no versions.source |
| microsoft/visual-basic-for-applications | 28 | aside | no versions.source |
| mirasvit/full-page-cache-warmer-for-magento-2 | 29 | closed source | https://mirasvit.com/package/changelog/?package=mirasvit%2Fmodule-cache-warmer: disallowed |
| mitel-networks/micollab | 30 | closed source | https://www.mitel.com/products/applications/collaboration/micollab: refused |
| nextgen-healthcare/mirth-connect | 31 | aside | no versions.source |
| nice-north-america/emerge-e3-series | 32 | aside | no versions.source |
| nrwl/nx-console | 34 | for the referee |  |
| nuuo/nvrmini-2 | 33 | aside | no versions.source |
| omnissa/workspace-one-uem | 35 | aside | no versions.source |
| palo-alto-networks/expedition | 37 | aside | no versions.source |
| plex/plex-media-server | 38 | aside | no versions.source |
| prettier/eslint-config-prettier | 39 | for the referee |  |
| ptc/flexplm | 40 | aside | no versions.source |
| qlik/qlik-sense | 41 | for the referee |  |
| sangoma/freepbx | 44 | aside | no versions.source |
| sap/sap-commerce-cloud | 45 | aside | no versions.source |
| siemens/siemens-software-center | 46 | for the referee |  |
| signalwire/freeswitch | 47 | for the referee |  |
| sitecore/sitecore-experience-manager-xm | 48 | parked | name not on the product page |
| sophos/cyberoamos | 49 | aside | no versions.source |
| sophos/sophos-web-appliance | 50 | aside | no versions.source |
| sunhillo/sureline | 51 | aside | no versions.source |
| teamt5/threatsonar-anti-ransomware | 52 | aside | no versions.source |
| the-apache-software-foundation/apache-superset | 5 | closed source | https://downloads.apache.org/superset/: disallowed |
| trend-micro/trend-micro-apex-one | 53 | parked | source not readable |
| trimble/cityworks | 54 | aside | no versions.source |
| twilio/authy | 55 | aside | no versions.source |
| versa-networks/versa-concerto | 56 | aside | no versions.source |

Cost of the referee: 12 proposals x $0.027 = $0.32.
Cost of the search: at most 5 closed pages x $0.011 = $0.06 (claude-haiku-5-5; the fee and about 12,000 input tokens of results each).
Cost of the grouping: 204 publishers, 997 names, ±$1.42 (at most $16.92).

| row | list | name | outcome | reason |
| --- | --- | --- | --- | --- |
| kev-versiepagina-55.csv:2 | Adobe: Flash Player | Adobe Flash Player | parked | no versions.source |
| kev-versiepagina-55.csv:3 | Advantive: VeraCore | VeraCore | parked | no versions.source |
| kev-versiepagina-55.csv:4 | AMI: MegaRAC SPx | MegaRAC SP-X | parked | no versions.source |
| kev-versiepagina-55.csv:5 | Apache: Superset | Apache Superset | parked | closed source; https://downloads.apache.org/superset/: disallowed |
| kev-versiepagina-55.csv:6 | Aquasecurity: Trivy | Trivy | candidate |  |
| kev-versiepagina-55.csv:7 | ASUS: Live Update | ASUS Live Update | parked | no versions.source |
| kev-versiepagina-55.csv:8 | Audinate: Dante Discovery | Dante Discovery | parked | no versions.source |
| kev-versiepagina-55.csv:9 | Cisco: BroadWorks | Cisco BroadWorks | candidate |  |
| kev-versiepagina-55.csv:10 | D-Link: DCS-2530L and DCS-2670L Devices | DCS-2670L | parked | no versions.source |
| kev-versiepagina-55.csv:11 | D-Link: DSL-2750B Devices | DSL-2750B | parked | no versions.source |
| kev-versiepagina-55.csv:12 | Dassault Systèmes: DELMIA Apriso | DELMIA Apriso | parked | no versions.source |
| kev-versiepagina-55.csv:13 | Dell: RecoverPoint for Virtual Machines (RP4VMs) | RecoverPoint for Virtual Machines | parked | closed source; https://www.dell.com/support/kbdoc/en-us/000376653: refused; the address it ended at, proposed: https://www.dell.com/en-us/dt/data-protection/recoverpoint.htm -> https://www.dell.com/en-us/learn/it-infrastructure/lp/cyber-resilience/data-protection-suite-recoverpoint-for-virtual-machines |
| kev-versiepagina-55.csv:14 | Digiever: DS-2105 Pro | DS-2105 Pro | parked | no versions.source |
| kev-versiepagina-55.csv:15 | Edimax: IC-7100 IP Camera | IC-7100 | parked | no versions.source |
| kev-versiepagina-55.csv:16 | Gladinet: CentreStack and Triofox | Triofox | parked | no versions.source |
| kev-versiepagina-55.csv:17 | Gladinet: Triofox | Triofox | parked | no versions.source |
| kev-versiepagina-55.csv:18 | Gogs: Gogs | Gogs | candidate | ; the address it ended at, proposed: https://gogs.io/ -> https://gogs.io/getting-started/introduction |
| kev-versiepagina-55.csv:19 | Google: Chromium V8 | V8 | candidate |  |
| kev-versiepagina-55.csv:20 | Google: Chromium WebRTC | WebRTC | parked | no versions.source |
| kev-versiepagina-55.csv:21 | GPUStack: GPUStack | GPUStack | candidate |  |
| kev-versiepagina-55.csv:22 | Hewlett Packard Enterprise (HPE): OneView | HPE OneView | parked | closed source; https://www.hpe.com/nl/en/software/oneview.html: disallowed |
| kev-versiepagina-55.csv:23 | Justice AV Solutions: Viewer | Viewer 8 | parked | no versions.source; the grouping: edition or range of justice-av-solutions/viewer, which is not in TAX and not proposed |
| kev-versiepagina-55.csv:24 | Laravel: Livewire | Livewire | candidate |  |
| kev-versiepagina-55.csv:25 | Microsoft: Partner Center | Partner Center | parked | no versions.source |
| kev-versiepagina-55.csv:26 | Microsoft: Power Pages | Power Pages | parked | no versions.source |
| kev-versiepagina-55.csv:27 | Microsoft: Publisher | Microsoft Publisher | parked | no versions.source |
| kev-versiepagina-55.csv:28 | Microsoft: Visual Basic for Applications (VBA) | Visual Basic for Applications | parked | no versions.source |
| kev-versiepagina-55.csv:29 | Mirasvit: Mirasvit Full Page Cache Warmer | Full Page Cache Warmer for Magento 2 | parked | closed source; https://mirasvit.com/package/changelog/?package=mirasvit%2Fmodule-cache-warmer: disallowed |
| kev-versiepagina-55.csv:30 | Mitel: MiCollab | MiCollab | parked | closed source; https://www.mitel.com/products/applications/collaboration/micollab: refused |
| kev-versiepagina-55.csv:31 | NextGen Healthcare: Mirth Connect | Mirth® Connect | parked | no versions.source |
| kev-versiepagina-55.csv:32 | Nice: Linear eMerge E3-Series | eMerge e3-Series | parked | no versions.source |
| kev-versiepagina-55.csv:33 | NUUO: NVRmini2 Devices | NVRmini 2 | parked | no versions.source |
| kev-versiepagina-55.csv:34 | Nx: Nx Console | Nx Console | candidate |  |
| kev-versiepagina-55.csv:35 | Omnissa: Workspace One UEM | Workspace ONE UEM | parked | no versions.source |
| kev-versiepagina-55.csv:36 | OSGeo: JAI-EXT | JAI-EXT | parked | no versions.source |
| kev-versiepagina-55.csv:37 | Palo Alto Networks: Expedition | Expedition | parked | no versions.source |
| kev-versiepagina-55.csv:38 | Plex: Media Server | Plex Media Server | parked | no versions.source |
| kev-versiepagina-55.csv:39 | Prettier: eslint-config-prettier | eslint-config-prettier | candidate |  |
| kev-versiepagina-55.csv:40 | PTC: Windchill and FlexPLM | FlexPLM | parked | no versions.source |
| kev-versiepagina-55.csv:41 | Qlik: Sense | Qlik Sense | candidate | ; the address it ended at, proposed: https://www.qlik.com/us/products/qlik-sense -> https://www.qlik.com/us/da/products/qlik-sense; https://help.qlik.com/sense/WhatsNew/What-is-new-QlikSense.htm -> https://help.qlik.com/en-US/sense/November2026/Content/Sense_Helpsites/WhatsNew/What-is-new-QlikSense.htm |
| kev-versiepagina-55.csv:42 | Ray-Project: Ray | Ray | candidate |  |
| kev-versiepagina-55.csv:43 | Rejetto: HTTP File Server | HFS ~ HTTP File Server | parked | name not on the product page; 'HFS ~ HTTP File Server' not on https://github.com/rejetto/hfs/releases/tag/v3.4.1 (the address https://github.com/rejetto/hfs/releases/latest ended at); the address it ended at, proposed: https://github.com/rejetto/hfs/releases/latest -> https://github.com/rejetto/hfs/releases/tag/v3.4.1 |
| kev-versiepagina-55.csv:44 | Sangoma: FreePBX | FreePBX | parked | no versions.source |
| kev-versiepagina-55.csv:45 | SAP: Commerce Cloud | SAP Commerce Cloud | parked | no versions.source |
| kev-versiepagina-55.csv:46 | Siemens: Software Center | Siemens Software Center | candidate |  |
| kev-versiepagina-55.csv:47 | SignalWire: FreeSwitch | FreeSWITCH | candidate | ; the address it ended at, proposed: https://www.signalwire.com/freeswitch -> https://signalwire.com/developers/freeswitch |
| kev-versiepagina-55.csv:48 | Sitecore: CMS and Experience Platform (XP) | Sitecore Experience Manager (XM) | parked | name not on the product page; 'Sitecore Experience Manager (XM)' not on https://www.sitecore.com/platform/experience-manager |
| kev-versiepagina-55.csv:49 | Sophos: CyberoamOS | CyberoamOS | parked | no versions.source |
| kev-versiepagina-55.csv:50 | Sophos: Web Appliance | Sophos Web Appliance | parked | no versions.source |
| kev-versiepagina-55.csv:51 | Sunhillo: SureLine | SureLine® | parked | no versions.source |
| kev-versiepagina-55.csv:52 | TeamT5: ThreatSonar Anti-Ransomware | ThreatSonar Anti-Ransomware | parked | no versions.source |
| kev-versiepagina-55.csv:53 | Trend Micro: Apex One | Trend Micro Apex One | parked | source not readable; https://docs.trendmicro.com/o-help/ent/apex-one/2019/en-us/apexOne_2019_server_readme.htm: unreadable |
| kev-versiepagina-55.csv:54 | Trimble: Cityworks | Cityworks | parked | no versions.source |
| kev-versiepagina-55.csv:55 | Twilio: Authy | Authy | parked | no versions.source |
| kev-versiepagina-55.csv:56 | Versa: Concerto | Versa Concerto | parked | no versions.source |
