# Referee validation sample — fill the `referee_label` column

Labels: relevant | marginal | irrelevant (see PROTOCOL.md defs).
Judge only the quote against the query, blind to labels.json.

| # | query_name | edge_id | predicate | source_quote | referee_label |
|---|---|---|---|---|---|
| 1 | hub_process_mining | 2472145089 | CONTAINS | - [Use] -- Choose a process to optimize, generate process data, and then get visualized and actionable insights. | marginal |
| 2 | hub_process_mining | 2466811894 | INTEGRATES_WITH | Proactively assess and improve ITSM CSM, Financial Services Operations, and HR Service Delivery processes. | marginal |
| 3 | hub_process_mining | 44121277 | USES_TABLE | Version [promin_model_def_version] \| The version of a mined project. | marginal |
| 4 | hub_process_mining | 5070159 | USES_TABLE | Breakdown Definition \| Stores the breakdowns by which the process map gets categorized. | marginal |
| 5 | hub_discovery | 43630656 | CONTAINS | To process the data returned from a multiprobe, you must create multisensors. | marginal |
| 6 | hub_discovery | 42979437 | CONTAINS | Discovery uses the Horizontal Pattern probe to launch patterns for horizontal discovery. | relevant |
| 7 | hub_discovery | 2470817993 | CONTAINS | glide.discovery.certs.enable_incident_creation_for_expired_certificates | marginal |
| 8 | hub_discovery | 43654316 | HAS_CI_TYPE | SAP Business Objects CMSserver [cmdb_ci_appl_sap_bo] | marginal |
| 9 | hub_cmdb | 2470523850 | USES_TABLE | Within the Base Configuration Item logical table there are records with class names for Application, Computer, and IP Router. | marginal |
| 10 | hub_cmdb | 43923330 | EXTENDS_TABLE | Base Configuration Item [cmdb] table contains all records from its child classes such as the Application [cmdb_ci_appl], Computer [cmdb_ci_computer], and Hardware [cmdb_ci_hardware] tables. | relevant |
| 11 | hub_cmdb | 2465810653 | EXTENDS_TABLE | Service [cmdb_ip_service_ci] | marginal |
| 12 | hub_cmdb | 4619285 | USES_TABLE | var ga = new GlideAggregate(tablename); ... var tableName = "cmdb"; | marginal |
| 13 | hub_incident | 43663772 | CREATES_DATA | Incident ... a record created in ServiceNow when correlated alerts point to a significant disruption in a service, requiring investigation and resolution. | relevant |
| 14 | hub_incident | 2047433755 | CREATES_DATA | When an alert must be escalated and assigned to someone who can resolve the underlying issue, you can open an incident. | relevant |
| 15 | hub_incident | 2466152162 | CONTAINS | Agents can seamlessly create incident, problem, change, and request records directly from open cases. | marginal |
| 16 | hub_incident | 1381592330 | CREATES_DATA | Use Incident Management to create an incident that captures information about the asset-related CIs. | marginal |
| 17 | control_no_hints_snake | 43928324 | USES_TABLE | Requested Item [sc_req_item] \| Number, Item, Stage, State, Requested for, Opened by, Opened | relevant |
| 18 | case2_minimal | 2467185100 | USES_TABLE | cirelatedlist = cdta.getRelatedListInstance('cmdb_rel_ci', 'parent'); // Get the CIRelatedList instance holding all relationships of the above CI. | marginal |
| 19 | case2_minimal | 2471528336 | USES_TABLE | For Many-to-many, the **Relationship** field shows the list of m2m tables in which the source table is referred. For example, [cmdb_rel_ci] table has **Parent** (cmdb_ci) and **Child** (cmdb_ci) fields. | marginal |
| 20 | case1_realistic_3hint | 43305898 | REQUIRES_PLUGIN | Install the ITSM Process Mining Content Pack from the ServiceNow Store. | relevant |
| 21 | hub_predictive_intelligence | 2094919502 | INTRODUCED_IN | The ServiceNow Predictive Intelligence application enables you to create and train machine learning models to help improve the performance, efficiency, and flexibility of your systems. Predictive Intelligence was enhanced and updated in the | relevant |
| 22 | hub_cmdb_rel_ci | 42945045 | USES_TABLE | Two CIs might be connected by one or more relationships (stored in the CI Relationship [cmdb_rel_ci] table). | relevant |
| 23 | hub_predictive_intelligence | 2471442692 | CONTAINS | With Predictive Intelligence, you can create machine learning solutions using historic datasets. | relevant |
| 24 | control_predicate_filter | 4846708 | REQUIRES_ROLE | Role required: discovery_admin | relevant |
| 25 | control_predicate_filter | 2465951700 | REQUIRES_ROLE | Role required: discovery_admin or cloud_root_admin | relevant |
| 26 | hub_predictive_intelligence | 2471165344 | CONTAINS | Predictive Intelligence provides three different model frameworks in the Australia release: classification, similarity, and clustering. | relevant |
| 27 | control_no_hints_snake | 43922430 | CREATES_DATA | Create a requested item [sc_req_item] on a Service Catalog Request [sc_request]. | relevant |
| 28 | hub_cmdb_rel_ci | 5040674 | USES_TABLE | The CI Relationship [cmdb_rel_ci] table provides a list of all relationships between configuration items. | relevant |
| 29 | control_predicate_filter | 2472362450 | REQUIRES_ROLE | Role required: pki_admin or discovery_admin | relevant |
| 30 | control_predicate_filter | 43028054 | REQUIRES_ROLE | PD MID ... The role enables the MID Server to interpret and run pattern- based probes. | relevant |
