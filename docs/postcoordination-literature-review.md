# Atomic Concepts, Post-Coordination, and Semantic Equivalence in Biomedical Terminology and Ontology Design: A Literature Review and Implementation Strategy for ONTOPRISM

**H. Niedner**
Niedner Consulting

*Working manuscript — prepared as design-informing background for the ONTOPRISM project. Version 1.0, July 2026.*

---

## Abstract

**Background.** Biomedical reference terminologies such as the NCI Thesaurus (NCIt), SNOMED CT, LOINC, and ICD have historically grown by *pre-coordination*: minting a distinct named concept for every clinically useful combination of meaning. Pre-coordination is convenient to author and query but scales combinatorially, buries semantics inside names, and couples every dimension of meaning to every other. An alternative design principle — build a terminology from a small set of *atomic* (primitive) concepts plus a formal *expression syntax* that *post-coordinates* those atoms at use time — has been advocated and partially realized across four decades of medical informatics research.

**Objective.** To review the peer-reviewed literature and authoritative standards on (i) the theoretical foundations of atomicity and compositionality, (ii) the mechanics of post-coordination in SNOMED CT, OWL/OBO ontologies, and clinical classifications, (iii) the machinery of *semantic equivalence* that lets a post-coordinated expression be recognized as equivalent to, or subsumed by, a pre-coordinated concept, and (iv) the recurring problem of *overloaded roles* — relationships that conflate multiple distinct senses. We then translate these findings into a concrete implementation strategy for ONTOPRISM, a platform that refactors NCIt's pre-coordinated concepts into their atomic constituents.

**Findings.** Three results recur across the literature. First, a fully-defined concept's logical definition is *always* an exact, lossless composition over primitives; the difficulty is never a missing atom but the *choice of relations* and the *canonical form* in which the composition is written. Second, description-logic (DL) classification — not string manipulation — is the reliable mechanism for testing whether a post-coordinated expression is equivalent to or subsumed by a named concept, which is precisely what preserves backward compatibility. Third, the value of the whole approach hinges on relations being *univocal*: a single role must denote a single, formally-defined relationship. NCIt violates this in ways we document with examples, and the mitigation — refactoring overloaded roles into univocal relations à la the OBO Relation Ontology, held in SNOMED-style relationship groups — is directly applicable to ONTOPRISM.

**Conclusion.** The design ONTOPRISM pursues (atoms + roles + a post-coordination grammar, additive and reversible over the stated OWL) is well-founded in the literature. Its principal risk is not decomposition coverage but relation quality; the review closes with a research-grounded mitigation for role overload, compared against ONTOPRISM's current additive genus-sense-classification approach.

---

## 1. Introduction

A controlled terminology serves two masters that pull in opposite directions. Clinicians and data systems want a *stable, enumerable* set of codes they can select, store, and count. Reality supplies an *open-ended, combinatorial* space of meanings — every disease crossed with every site, laterality, stage, causative agent, and temporal qualifier. The dominant historical response has been **pre-coordination**: whenever a combination is needed often enough, the terminology authors mint a new named concept for it. NCIt, for example, contains tens of thousands of such concepts; in ONTOPRISM's own audit of the stated OWL, 55,044 NCIt concepts carry two or more defining role restrictions (a corpus-wide count across all ~204K classes, not the smaller in-scope oncology candidate set), and near-duplicate pairs such as *"Stage III Thyroid Gland Medullary Carcinoma AJCC v7"* and *"…AJCC v8"* re-enumerate one clinical entity purely to track a staging-manual revision.

The alternative is **post-coordination**: keep the terminology's *atomic* concepts small in number and formally defined, and express complex meaning as *combinations* of atoms assembled at query or documentation time through a defined grammar of relationships. "Non–Small Cell Lung Carcinoma" is not a named node but the expression *Lung Carcinoma : has\_finding\_site = Lung, has\_associated\_morphology = Non–Small Cell Carcinoma*. The trade-off has been articulated repeatedly since the 1990s: post-coordination is vastly more flexible and avoids combinatorial bloat, but it demands (a) a rigorous concept model constraining which combinations are sensible, and (b) a reasoning mechanism that can recognize when two differently-written expressions mean the same thing, and when an expression falls under an existing named concept [3][7][10].

This review synthesizes the peer-reviewed and standards literature on that trade-off, with particular attention to the goal that motivates ONTOPRISM: *using only atomic terms plus an expression syntax to avoid pre-coordination entirely, while retaining the ability to map post-coordinated expressions back to pre-coordinated concepts by semantic equivalence.* Section 2 sets out the conceptual vocabulary and the classic "generations" of terminological systems. Section 3 covers the theoretical foundations of atomicity and compositionality. Section 4 surveys post-coordination mechanisms across SNOMED CT, OWL/OBO, and clinical classifications. Section 5 treats semantic equivalence: normal forms, DL classification, and subsumption. Section 6 documents the *overloaded-role* problem with NCIt examples and proposes a research-grounded mitigation. Section 7 assesses trade-offs, scalability, and quality assurance. Section 8 maps the entire review onto ONTOPRISM's implementation strategy.

---

## 2. Conceptual background and the "generations" of terminological systems

### 2.1 Concepts, terms, and coordination

A *concept* is a unit of meaning; a *term* is a linguistic label for it; a *code* is a language-independent identifier. de Keizer, Abu-Hanna and Zwetsloot-Schonk provide a widely-cited typology that standardizes this vocabulary and distinguishes terminological systems by their structure — nomenclatures, classifications, thesauri, and formally-defined systems [5]. *Coordination* refers to how a system packages multiple elementary meanings: **pre-coordination** binds them into one named code; **post-coordination** leaves them as separate atoms to be combined on demand.

### 2.2 Three generations

Rossi Mori, Consorti and Galeazzi introduced an influential developmental typology that frames the entire post-coordination programme [4]:

- **First generation** — terms are opaque strings; meaning lives only in human-readable labels (classical code lists, much of early ICD). Every combination must be pre-enumerated.
- **Second generation** — phrases are *dissected* into simpler component terms drawn from defined semantic categories ("categorical structures"), so that a complex meaning is represented as a coordinated set of atoms rather than one string.
- **Third generation** — *generative* systems in which a formal concept model automatically positions and groups new compositions, computing their place in the hierarchy rather than requiring an author to assert it.

ONTOPRISM's ambition — decompose NCIt's first/pre-coordinated content into second-generation atomic constituents, then serve them through a third-generation generative query layer — is exactly this progression, applied retrospectively to a terminology built in the pre-coordinated style.

### 2.3 Reference vs interface vs administrative terminologies

A parallel distinction, crystallized by Rosenbloom and colleagues, separates the *layers* a terminology plays [32]. A **reference terminology** (e.g., SNOMED CT) provides formal, compositional, machine-processable definitions. An **interface terminology** provides clinician-friendly colloquial phrases mapped onto the reference layer. An **administrative/classification terminology** (e.g., ICD) aggregates concepts into statistical categories for reporting and reimbursement. Pre- and post-coordination are properties of the reference layer; interface and administrative layers are *views* over it. This three-layer framing matters for ONTOPRISM because NCIt is used simultaneously as reference, browsing, and aggregation terminology, and the decomposition must preserve all three uses.

---

## 3. Theoretical foundations of atomicity and compositionality

### 3.1 Cimino's Desiderata

The canonical statement of terminology-design principles is Cimino's *Desiderata for Controlled Medical Vocabularies in the Twenty-First Century* [1], revisited and defended in 2006 [2]. Several desiderata directly motivate an atomic, compositional design:

- **Concept orientation** — each code denotes exactly one meaning, non-redundant and non-ambiguous. Atoms must be genuinely atomic.
- **Concept permanence** — meanings are never mutated or deleted, only retired. This is what lets a decomposed view coexist additively with legacy concepts.
- **Non-semantic identifiers** — codes carry no meaning in their structure; meaning lives in formal relationships.
- **Formal (compositional) definitions** — concepts should be defined by their relationships to other concepts, enabling machine reasoning.
- **Recognized redundancy** — where a pre-coordinated concept and a post-coordinated expression denote the same thing, that equivalence must be *explicit and computable*, not accidental.
- **Multiple granularities and polyhierarchy** — a concept may sit under several parents; the hierarchy should be inferred from definitions rather than hand-asserted.

Cimino's "recognized redundancy" is precisely the *semantic-equivalence mapping* requirement at the heart of ONTOPRISM: a decomposed expression must be linkable back to the pre-coordinated concept it reconstructs.

### 3.2 Why clinical terminology is hard

Rector's *Clinical Terminology: Why Is It So Hard?* [3] is the foundational analysis of the tension. He argues that the combinatorial richness of clinical language makes exhaustive pre-enumeration impossible in principle, and that the only sustainable path is a *generative* system with formal definitions — but that generativity introduces its own hazards (nonsensical compositions, ambiguous relations, the "linguistic vs conceptual" gap). This paper frames the central design question: not *whether* to compose from atoms, but *how to constrain* composition so it stays meaningful.

### 3.3 GALEN, GRAIL, and sanctioning

The GALEN project and its concept-modelling language GRAIL [7][8] are the most complete early realization of a purely compositional medical terminology. GRAIL builds all complex meaning from elementary concepts and a small set of relationships, and — crucially — introduces **sanctioning** to control combinatorial explosion. GRAIL distinguishes:

- **Grammatical sanction** — which attribute-value combinations are *syntactically sensible* (a *fracture* can *hasLocation* a *bone*; it cannot *hasLocation* an *emotion*), and
- **Sensible sanction** — which combinations are *clinically plausible* and should be offered to users.

Sanctioning is the mechanism that makes a generative terminology tractable: it permits the intended atomic combinations while blocking the astronomically larger space of nonsensical ones, *without* pre-enumerating the permitted set. SNOMED CT's Machine-Readable Concept Model (MRCM, §4.1) is a direct descendant of this idea, and any post-coordination grammar ONTOPRISM builds will need an equivalent sanctioning layer.

### 3.4 Compositionality in the OWL/OBO tradition

In the ontology-engineering tradition, the same principle appears as the distinction between **primitive** and **defined** classes. Rector's *Modularisation of domain ontologies* [22] — the "normalization" methodology — decomposes a tangled polyhierarchy into disjoint, homogeneous *primitive skeleton* taxonomies (each asserting only necessary conditions), then reconstructs the multi-parent hierarchy by giving *defined* classes necessary-and-sufficient conditions and letting a reasoner infer their placement. Stevens and Sattler give the clearest concise statement of the post-coordination thesis in this idiom: an ontology supplies atomic "building blocks", composite class expressions are assembled on the fly, and a reasoner determines the implicit relationship between such expressions and named classes — avoiding the combinatorial explosion of enumerating, say, disease × location × cause × status as named terms [23]. The OWL 2 Primer [21] is the normative reference for the class-expression machinery (existential and universal restrictions, intersection, equivalent-class vs subclass axioms) that makes this possible.

---

## 4. Mechanisms of post-coordination across systems

### 4.1 SNOMED CT

SNOMED CT is the most mature production system built for post-coordination, and its specifications are the authoritative reference for the mechanics.

**Compositional Grammar (SCG)** [18] defines the syntax for writing pre- and post-coordinated expressions as text: a *focus concept*, refined by *attribute–value* pairs, organized into *attribute (relationship) groups*, with nested expressions and concrete values. A pre-coordinated expression is simply the degenerate case — a single concept identifier with no refinement.

**The Concept Model and MRCM** [20] specify which refinements are *valid*: for each domain, which attributes apply, what their permitted value ranges are, and how they group. This is the computable form of GRAIL sanctioning: the MRCM is authored *in* the Expression Constraint Language and enforced by tooling so that only sensible post-coordinated expressions can be built [16].

**Expression Constraint Language (ECL)** [19] is the query/constraint language over SNOMED CT. ECL expresses bounded sets of concepts or expressions via hierarchy traversal (`<`, `<<`), attribute refinement, and boolean composition, and is the mechanism by which reference sets, value sets, and attribute ranges are defined. For ONTOPRISM, ECL is the model for the "compose" query layer: a post-coordinated data point is retrieved by an ECL-style constraint that names atoms and roles rather than a pre-coordinated code.

The origin of this architecture is SNOMED RT [9], the first version to give every concept a description-logic definition, explicitly separating the formal *reference* layer from human-facing views. Rector and Brandt [11] analyze the deliberate choice of the restricted **EL** family of description logics for SNOMED CT — polynomial-time classification at the cost of expressivity (no negation, disjunction, or full role composition) — and the representational compromises this forces, an important caveat for anyone relying on SNOMED-style DL to guarantee expression equivalence.

### 4.2 OWL and the OBO ecosystem

In OBO/OWL biomedical ontologies — whose role in knowledge management, data integration, and decision support is surveyed by Bodenreider [6] — post-coordination is realized as **logical definitions** (also called *cross-products*): a class is defined by an equivalent-class axiom composing terms from several orthogonal reference ontologies, and a reasoner infers its subsumption relationships automatically.

- The **OBO Foundry principles** [24] mandate *orthogonality* — non-overlapping reference ontologies with shared, well-defined relations — which is the precondition that makes cross-ontology composition non-redundant.
- The **Gene Ontology cross-product** work [26] gives GO classes explicit computable definitions referencing ChEBI, Cell Ontology, and Uberon, so that a reasoner reproduces and maintains the named GO hierarchy from composed primitives — an empirical demonstration that post-coordinated definitions can regenerate a pre-coordinated hierarchy.
- The **Entity–Quality (EQ)** model for phenotypes [27][28] represents a phenotype not as an enumerated atomic term but as an *entity* (from an anatomy or process ontology) bearing a *quality* (from PATO), enabling cross-species integration by reasoning over composed definitions — the seminal compositional-vs-enumerative argument in phenotype ontologies.
- **Ontology Design Patterns** [29] catalogue reusable modelling "building blocks" (including the Value Partition and Normalisation patterns), providing the methodological scaffolding for compositional class definition.

The OWL/OBO tradition is directly relevant to ONTOPRISM because NCIt *is* an OWL ontology, and its pre-coordination is encoded exactly as this literature would predict: as **defined classes** with `owl:equivalentClass` / `owl:intersectionOf` axioms intersecting a genus (named superclass) with one or more existential role restrictions.

### 4.3 LOINC, ICD-11, and FHIR

Clinical classifications illustrate the spectrum between the two poles.

**LOINC** is a *pre-coordinated code assembled from atomic parts*. Each LOINC term is a fully-specified name over six axes — Component, Property, Time, System (specimen), Scale, and Method — each drawn from a controlled set of atomic "LOINC Parts" [30]. The name is pre-coordinated (one code per combination), but the underlying part model is compositional, and the LOINC–SNOMED CT cooperative agreement has since exposed the parts as a reference set so LOINC terms can be re-expressed as SNOMED CT Observable-Entity expressions that preserve every axis-level atom.

**ICD-11** is explicitly bi-modal [31]. Its **Foundation Component** is a semantic knowledge base from which purpose-specific *linearizations* (e.g., Mortality and Morbidity Statistics) are derived. ICD-11 supports **post-coordination** through *clusters*: *stem codes* combined via a forward slash and *extension codes* (severity, laterality, temporality, aetiology, histopathology, etc.) attached via an ampersand. Critically for the semantic-equivalence theme, ICD-11 uses **sanctioning rules** — lookup tables that both permit valid post-coordinations and *map pre-coordinated stem codes to their post-coordinated equivalents* — a concrete production example of the pre-↔post equivalence mapping ONTOPRISM needs.

**HL7 FHIR terminology services** [33] operationalize the runtime layer: `CodeSystem`, `ValueSet`, and `ConceptMap` resources with operations for `$expand`, `$validate-code`, `$subsumes`, and `$translate`. FHIR admits SNOMED CT post-coordinated expressions as codes and provides `ConceptMap.$translate` as the standard cross-terminology equivalence mechanism — the interoperability substrate any decomposed terminology must ultimately serve.

---

## 5. Semantic equivalence: normal forms, classification, and subsumption

The defining requirement — express everything from atoms *and* still recognize when an expression equals or falls under a named concept — is a problem of **semantic equivalence and subsumption testing**. The literature converges on two complementary tools.

### 5.1 Normal forms

Because a single logical meaning can be written in many syntactically different but logically equivalent expressions, one needs *canonical* representations. Spackman's *Normal forms for description logic expressions of clinical concepts in SNOMED RT* [10] is the foundational treatment. It distinguishes a *choice of syntax* from a *choice of normal form* and defines a **short canonical (short normal) form**, a **long canonical (long normal) form**, and a **distribution normal form**. Transforming two expressions into the same normal form reduces equivalence testing to a structural comparison, and is how SNOMED CT release tooling computes its Necessary Normal Form — explicitly removing attributes that are redundant because a more specific value is asserted on an alternate hierarchy branch.

Normal forms directly ground ONTOPRISM's most-specific-filler policy (see §8): when the same axis carries candidate fillers at different specificity on different DAG branches, choosing the most specific true value is exactly the normalization SNOMED performs at production scale.

### 5.2 Description-logic classification

Normal forms alone are not always sufficient; the reliable general mechanism is **DL classification**. A post-coordinated expression is a defined class (necessary-and-sufficient conditions); a DL reasoner computes its position in the subsumption hierarchy, inferring that it is *equivalent to* a named concept, or *subsumed by* one, or a new intermediate. This is the exact operation that preserves backward compatibility: a data point coded as *Lung Carcinoma : has\_finding\_site = Lung, has\_associated\_morphology = Non–Small Cell Carcinoma* is retrieved by a query for the named concept "Non–Small Cell Lung Carcinoma" **because the reasoner subsumes the expression under the concept** [10][23][26].

Two caveats from the literature bound this mechanism:

- **Expressivity limits.** SNOMED's EL family cannot express negation, disjunction, or arbitrary role composition; some intended equivalences are therefore not derivable within it [11][13]. Schulz, Markó and Suntisrivaraporn analyze how complex expressions — context, negation, part–whole — must be formally represented to remain logically well-formed [13].
- **Subsumption can diverge from intent.** Bodenreider, Smith, Kumar and Burgun empirically show cases where SNOMED CT's *inferred* is-a hierarchy diverges from intended clinical meaning [12] — a warning that "the reasoner said so" is not automatically "clinically correct", and that inferred subsumption must be validated, not trusted blindly.

### 5.3 Scalability of classifying post-coordinated content

A live objection is whether classifying large volumes of post-coordinated expressions is computationally feasible. Karlsson, Nyström and Cornet measured reasoner classification time as increasing numbers of post-coordinated expressions were added to SNOMED CT and found it remains tractable with current EL reasoners [15] — empirical support that the generative approach scales to production data volumes, not merely to toy examples.

---

## 6. The overloaded-role problem: single-concept vs multi-concept relationships

### 6.1 Why relations, not atoms, are the hard part

A finding that recurs once decomposition is actually attempted — and one that ONTOPRISM has confirmed directly against the live NCIt store — is that **the scarce resource is not atomic concepts but univocal relations.** Every filler needed to reconstruct a defined NCIt concept is *already an existing, active NCIt concept*: ONTOPRISM's decomposition assessment records 100% coverage for the roles path, and its D17 decision verified that across the concepts examined, no needed atomic filler was ever missing. The difficulty lies elsewhere: NCIt's role vocabulary is **overloaded** — a single role identifier is reused for pragmatically distinct relationships, so that a "single-concept role-based relationship" (one atom connected to one atom by one well-defined relation) is silently carrying *two or more different senses*.

This is precisely the failure mode the OBO Relation Ontology was created to prevent. Smith, Ceusters and colleagues [25] argue that relations in biomedical ontologies must be given *consistent, unambiguous, formal definitions* — a relation like *part\_of* must mean one thing, with stated logical properties (transitivity, reflexivity, the classes it may connect), or reasoning and annotation errors follow. An overloaded role violates this at the root: it is not one relation but several wearing one label.

### 6.2 NCIt examples of role overload

ONTOPRISM's investigation (DECISIONS D16/D17/D19/D20; engine design §6.4–§6.6) surfaced concrete, reproducible instances against the stated OWL:

**(a) `R101` (primary site) conflates literal anatomic site with tumour-lineage classification.**
For a thyroid neoplasm such as `C6135`, the primary-site axis should resolve to the literal organ (Thyroid Gland). But the same `R101` restriction, `R101 → Endocrine Gland/System`, is inherited from the organ-agnostic tumour-family ancestor `C3010 "Endocrine Neoplasm"` — and that *identical* ancestor anchors the *same* restriction in `C35756`, a **lung** neuroendocrine tumour. One role identifier is therefore doing two jobs: asserting a literal finding site (Thyroid, Lung) *and* asserting a histogenetic/lineage classification (belongs to the endocrine-tumour family). The two senses are genuinely co-equal facts, not one being more specific than the other, so no most-specific rule can separate them.

**(b) `R105` (abnormal cell) shows the same lineage-vs-literal conflation.**
The neuroendocrine pattern recurs on the abnormal-cell axis: a cell-type filler inherited from a lineage-generic ancestor coexists with the literal cell type, again as two true-but-distinct senses under one role.

**(c) Region-vs-organ ties.**
On the same primary-site axis, NCIt models an anatomical *region* and the *organ* within it (e.g., *Colorectal Region* vs *Colon*; *Endocardium* vs *Left Atrium*) as siblings that the role does not relate by specificity — a second, structurally different flavour of overload where the relationship silently mixes granularities.

**(d) Part–whole overloading (the SNOMED precedent).**
The analogous problem in SNOMED CT is the historical **SEP-triplet** encoding, which simulated *part\_of* transitivity by overloading *is-a*. Suntisrivaraporn, Baader, Schulz and Spackman, and Schulz et al.'s broader "health check" of SNOMED, showed that overloading a subsumption relation to carry part–whole semantics is error-prone and is better replaced by a *directly and univocally defined* part-of relation with explicit logical properties [16]. NCIt's `R82` (*Anatomic\_Structure\_Is\_Physical\_Part\_Of*) is not transitively materialized, which is the same class of problem viewed from the opposite side.

The common thread: each of these is a *multi-concept overloading of a role* — the relationship, not the endpoint concepts, is the locus of ambiguity, and it defeats the clean "single atom —[single relation]→ single atom" model that post-coordination depends on.

### 6.3 A research-grounded mitigation

The literature points to a coherent, three-part mitigation.

1. **Refactor overloaded roles into univocal relations (OBO Relation Ontology methodology) [25].** Split `R101` into formally-defined sub-relations with disjoint, stated meanings — e.g., `has_literal_finding_site` (connects a neoplasm to the organ it is physically in) versus `has_histogenetic_lineage` (connects a neoplasm to the tumour family it is classified under). Each becomes a genuine single-sense relation with a stated domain, range, and logical properties, so that a "single-concept role-based relationship" once again denotes exactly one thing.

2. **Preserve co-equal senses as SNOMED-style relationship groups, not a forced single value [10][18].** Where two senses are genuinely co-equal (site *and* lineage), they are kept as distinct, simultaneously-asserted members of a relationship group — SNOMED CT's production mechanism for exactly this situation — rather than collapsed to one filler. This is the "recognized redundancy / multiple granularities" desideratum [1] realized structurally.

3. **Untangle by Rector normalization and let a reasoner re-derive the hierarchy [22].** Anatomy/site, morphology/cell, and lineage/classification are separated into disjoint primitive skeletons; the multi-parent placement that the overloaded role used to assert by hand is instead *inferred* from the univocal logical definitions by a DL reasoner. This removes the incentive to overload a role in the first place.

### 6.4 Comparison to ONTOPRISM's current approach

ONTOPRISM's decisions D17/D19/D20 adopt a deliberately *additive* variant of this mitigation rather than a global role-splitting rewrite. The two are compared below.

| Dimension | Research-founded refactor (RO-style role split) | ONTOPRISM current approach (D17/D19/D20) |
|---|---|---|
| **Mechanism** | Physically split overloaded `R101`/`R105` into new univocal relations across the whole ontology; regenerate the graph with split roles. | Keep NCIt's `R101`/`R105` triples untouched; *classify the anchoring genus concepts* (site-specific vs lineage/histology-generic) and store that classification additively as new metadata, consulted during per-level role extraction to route a restriction to its raw role or a new `op:` axis. |
| **Handling of co-equal senses** | Distinct univocal relations, held in relationship groups. | Distinct axes routed by genus-sense, held in D19 relationship groups (same target structure). |
| **Provenance / reversibility** | New graph; requires a mapping back to the original stated axioms to stay reversible. | Additive by construction — the stated OWL is never mutated; the lossless `owl:equivalentClass` unfolding remains the record of truth, and the classification is a projection over it. |
| **Scope of change** | Global rewrite of ~10⁷ stated triples; high blast radius. | Incremental — only the few hundred/thousand genus concepts that actually anchor decomposition-relevant restrictions are classified. |
| **Risk** | Correct in principle but a large, error-prone migration; risks diverging from NCIt releases and breaking backward compatibility. | Lower blast radius and fully reversible, but the overload is *annotated around* rather than eliminated — the raw ambiguous role persists for any consumer not using the projection. |
| **Reasoning dependency** | Univocal relations make DL subsumption cleaner. | Must contend with NCIt's incomplete `rdfs:subClassOf+` closure (D21): defined-class-to-defined-class subsumption is *not materialized*, so nestedness is only decidable where present, and the safe direction is to preserve (not collapse) uncertain pairs. |

The two approaches converge on the *same target representation* — univocal, single-sense relationships held in relationship groups — and differ only in whether that is achieved by rewriting NCIt or by an additive, reversible classification layered over it. ONTOPRISM's choice of the additive route is well-supported: it honours concept permanence [1], keeps the lossless `owl:equivalentClass` definition as the round-trippable artifact of record, and confines the lossy simplifications (single-most-specific filler, defining-axis allowlist) to a clearly-labelled *curated projection* on top. The research-founded refactor is best positioned not as a replacement but as the *eventual normalization target*: as the additive genus-sense classification matures and coverage grows, the accumulated classification is exactly the evidence base needed to define the univocal RO-style relations properly, should a future NCIt-independent release warrant them.

A critical constraint, recorded as D21 and consistent with Bodenreider et al.'s divergence findings [12], must gate either approach: because NCIt's inferred graph does *not* materialize defined-class subsumption, an OWL reasoner over the stated `owl:equivalentClass`/`owl:intersectionOf` structure (not `rdfs:subClassOf+`) must be the closure oracle before any round-trip-fidelity claim is made. Equivalence testing must use real DL classification, exactly as §5.2 prescribes.

---

## 7. Trade-offs, quality, and limitations

### 7.1 The combinatorial-explosion argument, quantified

The core case for post-coordination is that pre-enumeration does not scale: the number of clinically meaningful combinations grows multiplicatively in the number of dimensions, so any enumerated terminology is perpetually incomplete and perpetually bloated [3][23]. NCIt's own 55,044 multi-role concepts, and the AJCC v7/v8 duplication pattern, are local evidence of exactly this. Post-coordination replaces an intractable enumeration with a small atom set plus a grammar.

### 7.2 Costs of post-coordination

The literature is candid about the price. Data entry becomes harder without a curated interface layer [32]. Retrieval requires a reasoner rather than a hash lookup [10][15]. Expressivity limits of the chosen DL can block intended inferences [11][13]. And empirical studies of real post-coordinated databases — e.g., Campbell et al.'s post-coordinated histopathology store — find genuine gaps where the concept model cannot express what pathologists need [14]. Karlsson et al. temper the scalability fear with measured evidence that classification stays tractable [15], but the *authoring* burden of a correct, sanctioned concept model is real.

### 7.3 Quality assurance

Because compositional systems infer rather than assert much of their structure, *auditing* becomes essential. Zhu et al. survey auditing methods for controlled biomedical terminologies, organizing them into manual, systematic, and heuristic approaches targeting concept orientation, consistency, non-redundancy, and coverage [34]. The abstraction-network tradition (partial-area taxonomies and related visualizations) abstracts structural detail so auditors can spot the irregularities that flag likely modelling errors. For a decomposition engine, this literature argues that the *validation* step — checking that decomposed triples round-trip to the source concept and introduce no inconsistency — is not optional polish but a first-class component.

### 7.4 Interface and backward compatibility

Finally, the reference-vs-interface distinction [32] and Kaiser Permanente's Convergent Medical Terminology [17] (a poly-hierarchical DL knowledge base reconciling clinician-facing interface terms with a formally-defined reference layer, later donated to SNOMED International) both make the same point: a purely post-coordinated reference layer must be wrapped in curated interface views and retain resolvable links to legacy pre-coordinated concepts, or it will not be usable in practice. ONTOPRISM's "flag, never delete" additivity is the concrete expression of this requirement.

---

## 8. Implementation strategy for ONTOPRISM

This section maps the review onto ONTOPRISM's four-goal architecture (rich NCIt+caDSR explorer → decomposed atomic NCIt → balanced graph → post-coordination expression syntax). Each recommendation is tied to the literature above and to the project's own recorded decisions.

### 8.1 Decomposition (Goal 2): roles-first, atoms already exist

The literature's strongest positive finding for ONTOPRISM is that NCIt's pre-coordination is encoded exactly as OWL defined classes [21][24], and a defined class's `owl:equivalentClass` unfolding is *always* a lossless composition over existing primitives [10][22]. This underwrites the project's *roles-first* extraction (100% filler coverage) and its framing of decomposition as **surfacing and re-linking, not inventing**. The recommendation is to treat the full multi-parent-DAG unfolding of the equivalent-class definition as the **lossless representation of record**, and any single-most-specific-filler view as a clearly-labelled **lossy curated projection** on top — exactly the D19 architecture, and directly justified by normal-form theory [10] and Rector normalization [22].

### 8.2 Relations before coverage: fix role overload first

The most important strategic implication of this review is a *sequencing* one. ONTOPRISM's open work is dominated by extractor coverage (currently ~3.24% on the naive baseline). The literature suggests that **relation quality gates decomposition quality**: because the scarce resource is univocal relations, not atoms (§6.1), pushing coverage on top of overloaded roles will propagate the `R101`/`R105` conflation into every decomposed concept. The recommendation is to prioritize the D17/D20 genus-sense classification (routing overloaded restrictions to distinct axes) as a *precondition* for coverage expansion, and to adopt the OBO Relation Ontology discipline [25] — every axis ONTOPRISM emits should correspond to a single, formally-defined relation with a stated domain, range, and logical properties. Co-equal senses go into D19 relationship groups [18], never a forced single value.

### 8.3 Semantic equivalence and round-trip fidelity: use a real reasoner

To satisfy Goal 2's reversibility requirement and Cimino's "recognized redundancy" [1], ONTOPRISM must be able to prove that a decomposed expression reconstructs its source concept. The review is unambiguous that this requires **DL classification, not string or `rdfs:subClassOf+` comparison** [10][12][23]. Consistent with D21, the closure oracle must be computed either from the stated `owl:equivalentClass`/`owl:intersectionOf` structure (which is complete by definition) or by running a real OWL reasoner over the stated build; the inferred `rdfs:subClassOf+` graph must *not* be used as a fidelity oracle, because it does not materialize defined-class subsumption and would report false negatives on exactly the chains reversibility depends on. This is the single highest-risk correctness item and should gate the `--emit-equivalence` seam.

### 8.4 Sanctioning and the post-coordination grammar (Goal 4)

When ONTOPRISM builds its post-coordination expression syntax, it should not invent one. SNOMED CT's Compositional Grammar [18], Expression Constraint Language [19], and Machine-Readable Concept Model [20] provide a proven, standards-based template: SCG for writing expressions, MRCM for *sanctioning* which refinements are valid (the computable descendant of GRAIL sanctioning [7]), and ECL for the query layer that retrieves post-coordinated data by naming atoms and roles. Aligning ONTOPRISM's grammar with SCG/ECL also buys interoperability with the wider ecosystem and a clean path to FHIR terminology services [33] via `ConceptMap.$translate` for the pre-↔post equivalence mapping — the same pattern ICD-11's sanctioning tables implement [31].

### 8.5 Backward compatibility, interface layer, and QA

Three supporting recommendations follow from §7. First, retain resolvable links from every decomposed expression to its legacy pre-coordinated concept (`representationStatus="legacy-precoordinated"`), honouring concept permanence [1] and the CMT/interface-terminology lesson that a reference layer needs curated views to be usable [17][32]. Second, build the *validation* step as a first-class engine component, drawing on the terminology-auditing literature [34] to check round-trip fidelity, non-redundancy, and consistency on every run. Third, expose the decomposed graph as an *additional lens*, never a replacement — the additivity that D19/D21 already enforce is not merely an engineering convenience but the literature-endorsed way to migrate a pre-coordinated terminology without breaking its existing consumers.

### 8.6 Summary of recommendations

The review supports ONTOPRISM's core thesis and sharpens its priorities. In order: (1) treat the lossless `owl:equivalentClass` unfolding as the record of truth and the most-specific view as a labelled projection [10][22]; (2) fix role overload with univocal relations and relationship groups *before* chasing coverage [18][25]; (3) prove equivalence with a real DL reasoner over the stated definitional structure, never `rdfs:subClassOf+` [10][12]; (4) model the post-coordination grammar on SCG/ECL/MRCM with an explicit sanctioning layer [7][18][19][20]; and (5) keep the whole thing additive, interface-wrapped, and continuously audited [1][17][34].

---

## 9. Conclusion

Four decades of medical-informatics and ontology-engineering research converge on a consistent picture. Building a terminology from atomic concepts plus a post-coordination grammar is not only feasible but, for combinatorially rich domains such as oncology, the only design that scales — provided three conditions hold: the concept model *sanctions* which compositions are meaningful [7][18]; semantic equivalence to pre-coordinated concepts is established by *description-logic classification*, not string comparison [10][23]; and the relationships connecting atoms are *univocal*, each denoting exactly one formally-defined sense [25]. NCIt satisfies the first condition partially and violates the third through overloaded roles, which we documented with concrete examples and addressed with a research-grounded mitigation compared against ONTOPRISM's additive genus-sense-classification approach. The two approaches share a target — single-sense relations in relationship groups — and ONTOPRISM's reversible, additive route is the more defensible migration path, with the OBO Relation Ontology refactor positioned as its eventual normalization target. The project's principal residual risk is not decomposition coverage but relation quality and the correctness of its equivalence oracle; prioritizing those, as this review recommends, is what will make the decomposed NCIt both faithful and useful.

---

## References

1. Cimino JJ. Desiderata for Controlled Medical Vocabularies in the Twenty-First Century. *Methods Inf Med.* 1998;37(4-5):394–403. doi:10.1055/s-0038-1634558. https://doi.org/10.1055/s-0038-1634558 (full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC3415631/)

2. Cimino JJ. In defense of the Desiderata. *J Biomed Inform.* 2006;39(3):299–306. doi:10.1016/j.jbi.2005.11.008. https://doi.org/10.1016/j.jbi.2005.11.008

3. Rector AL. Clinical Terminology: Why Is It So Hard? *Methods Inf Med.* 1999;38(4-5):239–252. doi:10.1055/s-0038-1634418. https://doi.org/10.1055/s-0038-1634418

4. Rossi Mori A, Consorti F, Galeazzi E. Standards to support development of terminological systems for healthcare telematics. *Methods Inf Med.* 1998;37(4-5):551–563. doi:10.1055/s-0038-1634542. https://doi.org/10.1055/s-0038-1634542

5. de Keizer NF, Abu-Hanna A, Zwetsloot-Schonk JHM. Understanding Terminological Systems I: Terminology and Typology. *Methods Inf Med.* 2000;39(1):16–21. doi:10.1055/s-0038-1634257. https://doi.org/10.1055/s-0038-1634257

6. Bodenreider O. Biomedical Ontologies in Action: Role in Knowledge Management, Data Integration and Decision Support. *Yearb Med Inform.* 2008:67–79. doi:10.1055/s-0038-1638585. https://doi.org/10.1055/s-0038-1638585

7. Rector AL, Bechhofer S, Goble CA, Horrocks I, Nowlan WA, Solomon WD. The GRAIL concept modelling language for medical terminology. *Artif Intell Med.* 1997;9(2):139–171. doi:10.1016/S0933-3657(96)00369-7. https://doi.org/10.1016/S0933-3657(96)00369-7

8. Rector AL, Nowlan WA, Glowinski A. Goals for concept representation in the GALEN project. *Proc Annu Symp Comput Appl Med Care (SCAMC).* 1993:414–418. https://pmc.ncbi.nlm.nih.gov/articles/PMC2248542/

9. Spackman KA, Campbell KE, Côté RA. SNOMED RT: A Reference Terminology for Health Care. *Proc AMIA Annu Fall Symp.* 1997:640–644. https://pmc.ncbi.nlm.nih.gov/articles/PMC2233423/

10. Spackman KA. Normal forms for description logic expressions of clinical concepts in SNOMED RT. *Proc AMIA Symp.* 2001:627–631. PMID:11825261. https://pmc.ncbi.nlm.nih.gov/articles/PMC2243264/

11. Rector AL, Brandt S. Why Do It the Hard Way? The Case for an Expressive Description Logic for SNOMED. *J Am Med Inform Assoc.* 2008;15(6):744–751. doi:10.1197/jamia.M2797. https://doi.org/10.1197/jamia.M2797 (full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC2585532/)

12. Bodenreider O, Smith B, Kumar A, Burgun A. Investigating subsumption in SNOMED CT: An exploration into large description logic-based biomedical terminologies. *Artif Intell Med.* 2007;39(3):183–195. doi:10.1016/j.artmed.2006.12.003. https://doi.org/10.1016/j.artmed.2006.12.003 (full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC2442845/)

13. Schulz S, Markó K, Suntisrivaraporn B. Formal representation of complex SNOMED CT expressions. *BMC Med Inform Decis Mak.* 2008;8(Suppl 1):S9. doi:10.1186/1472-6947-8-S1-S9. https://doi.org/10.1186/1472-6947-8-S1-S9

14. Campbell WS, Campbell JR, West WW, McClay JC, Hinrichs SH. Semantic analysis of SNOMED CT for a post-coordinated database of histopathology findings. *J Am Med Inform Assoc.* 2014;21(5):885–892. doi:10.1136/amiajnl-2013-002456. https://doi.org/10.1136/amiajnl-2013-002456 (full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC4147616/)

15. Karlsson D, Nyström M, Cornet R. Does SNOMED CT post-coordination scale? *Stud Health Technol Inform.* 2014;205:1048–1052. PMID:25160348. doi:10.3233/978-1-61499-432-9-1048. https://doi.org/10.3233/978-1-61499-432-9-1048

16. Schulz S, Suntisrivaraporn B, Baader F, Boeker M. SNOMED reaching its adolescence: Ontologists' and logicians' health check. *Int J Med Inform.* 2009;78(Suppl 1):S86–S94. doi:10.1016/j.ijmedinf.2008.06.004. https://doi.org/10.1016/j.ijmedinf.2008.06.004

17. Dolin RH, Mattison JE, Cohn S, et al. Kaiser Permanente's Convergent Medical Terminology. *Stud Health Technol Inform (MEDINFO).* 2004;107(Pt 1):346–350. PMID:15360832. https://pubmed.ncbi.nlm.nih.gov/15360832/

18. SNOMED International. *SNOMED CT Compositional Grammar — Specification and Guide.* https://docs.snomed.org/snomed-ct-specifications/snomed-ct-compositional-grammar-specification

19. SNOMED International. *SNOMED CT Expression Constraint Language — Specification and Guide.* https://docs.snomed.org/snomed-ct-specifications/snomed-ct-expression-constraint-language

20. SNOMED International. *SNOMED CT Machine Readable Concept Model (MRCM) — Specification.* https://docs.snomed.org/snomed-ct-specifications/snomed-ct-machine-readable-concept-model

21. Hitzler P, Krötzsch M, Parsia B, Patel-Schneider PF, Rudolph S (eds). *OWL 2 Web Ontology Language Primer (Second Edition).* W3C Recommendation, 11 December 2012. https://www.w3.org/TR/owl2-primer/

22. Rector AL. Modularisation of domain ontologies implemented in description logics and related formalisms including OWL. *Proc. 2nd Int. Conf. on Knowledge Capture (K-CAP '03).* 2003:121–128. doi:10.1145/945645.945664. https://doi.org/10.1145/945645.945664

23. Stevens R, Sattler U. Post-coordination: Making things up as you go along. *Ontogenesis.* 5 April 2013. https://ontogenesis.knowledgeblog.org/1305/

24. Smith B, Ashburner M, Rosse C, et al. The OBO Foundry: coordinated evolution of ontologies to support biomedical data integration. *Nat Biotechnol.* 2007;25(11):1251–1255. doi:10.1038/nbt1346. https://doi.org/10.1038/nbt1346

25. Smith B, Ceusters W, Klagges B, et al. Relations in biomedical ontologies. *Genome Biol.* 2005;6(5):R46. doi:10.1186/gb-2005-6-5-r46. https://doi.org/10.1186/gb-2005-6-5-r46 (full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC1175958/)

26. Mungall CJ, Bada M, Berardini TZ, Deegan J, Ireland A, Harris MA, Hill DP, Lomax J. Cross-product extensions of the Gene Ontology. *J Biomed Inform.* 2011;44(1):80–86. doi:10.1016/j.jbi.2010.02.002. https://doi.org/10.1016/j.jbi.2010.02.002 (full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC2910209/)

27. Mungall CJ, Gkoutos GV, Smith CL, Haendel MA, Lewis SE, Ashburner M. Integrating phenotype ontologies across multiple species. *Genome Biol.* 2010;11(1):R2. doi:10.1186/gb-2010-11-1-r2. https://doi.org/10.1186/gb-2010-11-1-r2

28. Gkoutos GV, Green ECJ, Mallon A-M, Hancock JM, Davidson D. Using ontologies to describe mouse phenotypes. *Genome Biol.* 2005;6(1):R8. doi:10.1186/gb-2004-6-1-r8. https://doi.org/10.1186/gb-2004-6-1-r8

29. Gangemi A, Presutti V. Ontology Design Patterns. In: Staab S, Studer R (eds), *Handbook on Ontologies* (2nd ed.), Springer, 2009:221–243. doi:10.1007/978-3-540-92673-3_10. https://doi.org/10.1007/978-3-540-92673-3_10

30. McDonald CJ, Huff SM, Suico JG, et al. LOINC, a Universal Standard for Identifying Laboratory Observations: A 5-Year Update. *Clin Chem.* 2003;49(4):624–633. doi:10.1373/49.4.624. https://doi.org/10.1373/49.4.624

31. Mabon K, Steinum O, Chute CG. Postcoordination of codes in ICD-11. *BMC Med Inform Decis Mak.* 2022;21(Suppl 6):379. doi:10.1186/s12911-022-01876-9. https://doi.org/10.1186/s12911-022-01876-9

32. Rosenbloom ST, Miller RA, Johnson KB, Elkin PL, Brown SH. Interface Terminologies: Facilitating Direct Entry of Clinical Data into Electronic Health Record Systems. *J Am Med Inform Assoc.* 2006;13(3):277–288. doi:10.1197/jamia.M1957. https://doi.org/10.1197/jamia.M1957 (full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC1513664/)

33. Health Level Seven International. *HL7 FHIR Release 4 — Terminology Service.* https://www.hl7.org/fhir/R4/terminology-service.html

34. Zhu X, Fan J-W, Baorto DM, Weng C, Cimino JJ. A review of auditing methods applied to the content of controlled biomedical terminologies. *J Biomed Inform.* 2009;42(3):413–425. doi:10.1016/j.jbi.2009.03.003. https://doi.org/10.1016/j.jbi.2009.03.003

---

*Prepared for the ONTOPRISM project. Cross-references to project decisions (D14–D21) and design documents (`docs/design/`) are internal and reflect the state of the repository as of July 2026.*
