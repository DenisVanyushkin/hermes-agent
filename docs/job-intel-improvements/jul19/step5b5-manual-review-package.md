# 5B-5 Manual Review Package — divergent decisions

Для каждого кейса: рекомендация det vs llm и наблюдения LLM (excerpt → факты).
Вопрос ревью: наблюдение — реальный факт вакансии (пробел аннотации) или натяжка?

## dataset: golden
### affirm_remote_us_timezone_gap: det=unclear -> llm=promising
- [mandate.scope_breadth] «Affirm's Enterprise Risk team helps the company make better business decisions by bringing an enterprise-wide view of the risks»
- [mandate.mandate_summary] «Affirm's Enterprise Risk team helps the company make better business decisions by bringing an enterprise-wide view of the risks»
- [mandate.strategy_ownership] «Senior Director, Enterprise Risk Strategy»

### airwallex_gpni: det=unclear -> llm=not_recommended
- [company.platform_ecosystem] «Airwallex is the only unified payments and financial platform for global businesses.»
- [company.customer_model] «we empower over 200,000 businesses worldwide»
- [company.product_culture_signal] «We hire successful builders with founder-like energy who want real impact, accelerated learning, and true ownership.»
- [company.scale] «across 26 offices around the globe.»
- [company.stage] «Valued at US$8 billion and backed by world-leading investors including T. Rowe Price, Visa, Mastercar»
- [mandate.platform_engineering] «GTPN is Airwallex’s core money-movement platform – it powers how customers collect money, manage t»
- [mandate.revenue_proximity] «GTPN is Airwallex’s core money-movement platform – it powers how customers collect money, manage t»
- [mandate.scope_breadth] «GTPN is Airwallex’s core money-movement platform»

### airwallex_payment_fraud: det=not_recommended -> llm=promising
- [company.customer_model] «unified payments and financial platform for global businesses.»
- [mandate.platform_as_business] «unified payments and financial platform for global businesses.»
- [company.scale] «over 200,000 businesses worldwide»
- [mandate.zero_to_one_mandate] «building out our Anti-Fraud capabilities»
- [mandate.revenue_proximity] «protect our customers from Payment fraud.»
- [mandate.scope_breadth] «Risk Platform team, building out our Anti-Fraud capabilities»
- [company.product_culture_signal] «iterating and pushing boundaries to deliver exceptional product experiences.»
- [company.stage] «Valued at US$8 billion»

### block_strategic_product_sales: det=unclear -> llm=promising
- [mandate.executive_exposure] «Head of Strategic Product Sales»
- [company.scale] «helping sellers worldwide do the same.»
- [mandate.team_build_mandate] «The Head of Strategic Product Sales will build and lead Block’s Product Sales organization»
- [organization.cross_functional_leadership] «a specialized overlay sales team»
- [company.platform_ecosystem] «Square makes commerce and financial services accessible to sellers. Cash App is the easy way to spend, send, and store money. Afterpay is transforming the way c»

### brex_growth_ai_vancouver: det=unclear -> llm=promising
- [mandate.growth_mandate] «Director of Product, Growth/AI»
- [company.scale] «more than 200 markets.»
- [company.customer_model] «global corporate cards and banking»
- [mandate.growth_mandate] «Growth Product team — overseeing the thoughtful strategy and execution of team and technical systems to drive customer acquisition and onboarding.»
- [mandate.revenue_proximity] «Growth Product team — overseeing the thoughtful strategy and execution of team and technical systems to drive customer acquisition and onboarding.»
- [organization.cross_functional_leadership] «collaborate closely with our Go-to-Market (GTM) teams»
- [mandate.team_build_mandate] «overseeing the thoughtful strategy and execution of team and technical systems»
- [mandate.feature_delivery_only] «overseeing the thoughtful strategy and execution of team and technical systems»
- [mandate.scope_breadth] «Growth Product team — overseeing the thoughtful strategy and execution of team and technical systems to drive customer acquisition and onboarding.»
- [company.product_culture_signal] «Brex’s AI-native automation and world-class service eliminate ma»

### monzo_business_banking: det=unclear -> llm=promising
- [mandate.scope_breadth] «Senior Product Director, Business Banking»
- [company.scale] «our product offering has grown a lot in the last 10 years in the UK.»
- [company.customer_model] «As well as personal and business bank accounts»
- [mandate.mandate_summary] «Define and deliver the future of how UK businesses manage their money.»
- [mandate.transformation_phase] «Define and deliver the future of how UK businesses manage their money.»
- [organization.cross_functional_leadership] «ambiguity, and can inspire multiple teams to deliver world-class outcomes.»
- [mandate.team_build_mandate] «building high-performing teams along the way»

### okx_internal_hr_finance: det=unclear -> llm=not_recommended
- [mandate.internal_tools_backoffice] «Product Director, Internal HR & Finance Systems»
- [company.is_crypto_exchange] «OKX is a leading crypto exchange»
- [mandate.strategy_ownership] «We are looking for a Product Director to unify and lead OKX's internal HR and Finance system product lines.»
- [mandate.mandate_summary] «This is a leadership role designed to bring strategic coherence to two critical»
- [organization.cross_functional_leadership] «across multiple departments and time zones»
- [mandate.team_build_mandate] «Team management experience (3+ people)»
- [company.scale] «brings the value of Blockchain to users around the world»
- [mandate.revenue_proximity] «Internal HR & Finance Systems»

### payoneer_core_ai_platform_israel: det=unclear -> llm=not_recommended
- [mandate.platform_engineering] «Senior Product Director, Core AI Platform»
- [company.platform_ecosystem] «Payoneer is the global financial platform that removes friction from doing business across borders»
- [company.scale] «over 2,500 colleagues all over the world»
- [company.product_culture_signal] «Payoneer is transforming into an AI-native company - where AI is not just a capability, but a core execution layer embedded across how we build products, operat»
- [mandate.transformation_phase] «Payoneer is transforming into an AI-native company - where AI is not just a capability, but a core execution layer embedded across how we build products, operat»

### synthetic_us_onsite_with_sponsorship: det=unclear -> llm=not_recommended
- [mandate.strategy_ownership] «Director of Product»
- [mandate.platform_engineering] «Payments Platform»

### wise_onboarding_experience: det=unclear -> llm=not_recommended
- [mandate.scope_breadth] «Product Lead - Onboarding Experience»
- [mandate.mandate_summary] «Product Lead - Onboarding Experience»

### wise_pricing: det=unclear -> llm=not_recommended
- [mandate.pricing_core] «Product Lead - Pricing»
- [mandate.scope_breadth] «Product Lead - Pricing»

## dataset: decision
### gd_affirm_remote_tz_gap: det=unclear -> llm=promising
- [mandate.scope_breadth] «Affirm's Enterprise Risk team helps the company make better business decisions by bringing an enterprise-wide view of the risks»
- [mandate.mandate_summary] «Affirm's Enterprise Risk team helps the company make better business decisions by bringing an enterprise-wide view of the risks»
- [mandate.strategy_ownership] «Senior Director, Enterprise Risk Strategy»

### gd_airwallex_gpni: det=unclear -> llm=not_recommended
- [company.platform_ecosystem] «Airwallex is the only unified payments and financial platform for global businesses.»
- [company.customer_model] «we empower over 200,000 businesses worldwide»
- [company.product_culture_signal] «We hire successful builders with founder-like energy who want real impact, accelerated learning, and true ownership.»
- [company.scale] «across 26 offices around the globe.»
- [company.stage] «Valued at US$8 billion and backed by world-leading investors including T. Rowe Price, Visa, Mastercar»
- [mandate.platform_engineering] «GTPN is Airwallex’s core money-movement platform – it powers how customers collect money, manage t»
- [mandate.revenue_proximity] «GTPN is Airwallex’s core money-movement platform – it powers how customers collect money, manage t»
- [mandate.scope_breadth] «GTPN is Airwallex’s core money-movement platform»

### gd_airwallex_payment_fraud: det=not_recommended -> llm=promising
- [company.customer_model] «unified payments and financial platform for global businesses.»
- [mandate.platform_as_business] «unified payments and financial platform for global businesses.»
- [company.scale] «over 200,000 businesses worldwide»
- [mandate.zero_to_one_mandate] «building out our Anti-Fraud capabilities»
- [mandate.revenue_proximity] «protect our customers from Payment fraud.»
- [mandate.scope_breadth] «Risk Platform team, building out our Anti-Fraud capabilities»
- [company.product_culture_signal] «iterating and pushing boundaries to deliver exceptional product experiences.»
- [company.stage] «Valued at US$8 billion»

### gd_block_sales_only: det=unclear -> llm=promising
- [mandate.executive_exposure] «Head of Strategic Product Sales»
- [company.scale] «helping sellers worldwide do the same.»
- [mandate.team_build_mandate] «The Head of Strategic Product Sales will build and lead Block’s Product Sales organization»
- [organization.cross_functional_leadership] «a specialized overlay sales team»
- [company.platform_ecosystem] «Square makes commerce and financial services accessible to sellers. Cash App is the easy way to spend, send, and store money. Afterpay is transforming the way c»

### gd_brex_growth_vancouver: det=unclear -> llm=promising
- [mandate.growth_mandate] «Director of Product, Growth/AI»
- [company.scale] «more than 200 markets.»
- [company.customer_model] «global corporate cards and banking»
- [mandate.growth_mandate] «Growth Product team — overseeing the thoughtful strategy and execution of team and technical systems to drive customer acquisition and onboarding.»
- [mandate.revenue_proximity] «Growth Product team — overseeing the thoughtful strategy and execution of team and technical systems to drive customer acquisition and onboarding.»
- [organization.cross_functional_leadership] «collaborate closely with our Go-to-Market (GTM) teams»
- [mandate.team_build_mandate] «overseeing the thoughtful strategy and execution of team and technical systems»
- [mandate.feature_delivery_only] «overseeing the thoughtful strategy and execution of team and technical systems»
- [mandate.scope_breadth] «Growth Product team — overseeing the thoughtful strategy and execution of team and technical systems to drive customer acquisition and onboarding.»
- [company.product_culture_signal] «Brex’s AI-native automation and world-class service eliminate ma»

### gd_monzo_business_banking: det=unclear -> llm=promising
- [mandate.scope_breadth] «Senior Product Director, Business Banking»
- [company.scale] «our product offering has grown a lot in the last 10 years in the UK.»
- [company.customer_model] «As well as personal and business bank accounts»
- [mandate.mandate_summary] «Define and deliver the future of how UK businesses manage their money.»
- [mandate.transformation_phase] «Define and deliver the future of how UK businesses manage their money.»
- [organization.cross_functional_leadership] «ambiguity, and can inspire multiple teams to deliver world-class outcomes.»
- [mandate.team_build_mandate] «building high-performing teams along the way»

### gd_okx_internal_tools: det=unclear -> llm=not_recommended
- [mandate.internal_tools_backoffice] «Product Director, Internal HR & Finance Systems»
- [company.is_crypto_exchange] «OKX is a leading crypto exchange»
- [mandate.strategy_ownership] «We are looking for a Product Director to unify and lead OKX's internal HR and Finance system product lines.»
- [mandate.mandate_summary] «This is a leadership role designed to bring strategic coherence to two critical»
- [organization.cross_functional_leadership] «across multiple departments and time zones»
- [mandate.team_build_mandate] «Team management experience (3+ people)»
- [company.scale] «brings the value of Blockchain to users around the world»
- [mandate.revenue_proximity] «Internal HR & Finance Systems»

### gd_payoneer_israel_unknown_sponsorship: det=unclear -> llm=not_recommended
- [mandate.platform_engineering] «Senior Product Director, Core AI Platform»
- [company.platform_ecosystem] «Payoneer is the global financial platform that removes friction from doing business across borders»
- [company.scale] «over 2,500 colleagues all over the world»
- [company.product_culture_signal] «Payoneer is transforming into an AI-native company - where AI is not just a capability, but a core execution layer embedded across how we build products, operat»
- [mandate.transformation_phase] «Payoneer is transforming into an AI-native company - where AI is not just a capability, but a core execution layer embedded across how we build products, operat»

### gd_us_onsite_with_sponsorship: det=unclear -> llm=not_recommended
- [mandate.strategy_ownership] «Director of Product»
- [mandate.platform_engineering] «Payments Platform»

### gd_wise_onboarding: det=unclear -> llm=not_recommended
- [mandate.scope_breadth] «Product Lead - Onboarding Experience»
- [mandate.mandate_summary] «Product Lead - Onboarding Experience»

### gd_wise_pricing: det=unclear -> llm=not_recommended
- [mandate.pricing_core] «Product Lead - Pricing»
- [mandate.scope_breadth] «Product Lead - Pricing»
