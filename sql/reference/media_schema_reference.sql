--
-- PostgreSQL database dump
--

\restrict rcrf8cODGynrrAQDl8gkyUAnBNo65MJt2yirjZjHhrIaK5C4tceVfy7ZBdaR0Th

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: media; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA media;


--
-- Name: SCHEMA media; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA media IS 'for the TOTR project  to store media including audio , images , and video ';


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: media; Owner: -
--

CREATE FUNCTION media.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: item_relations; Type: TABLE; Schema: media; Owner: -
--

CREATE TABLE media.item_relations (
    relation_id integer NOT NULL,
    from_item_id integer NOT NULL,
    relation_type_id integer NOT NULL,
    to_item_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: item_relations_relation_id_seq; Type: SEQUENCE; Schema: media; Owner: -
--

ALTER TABLE media.item_relations ALTER COLUMN relation_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME media.item_relations_relation_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: item_types; Type: TABLE; Schema: media; Owner: -
--

CREATE TABLE media.item_types (
    type_id integer NOT NULL,
    data_id uuid DEFAULT gen_random_uuid() NOT NULL,
    item_type_slug character varying(100) NOT NULL,
    description text,
    parent_type_id integer,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: item_types_type_id_seq; Type: SEQUENCE; Schema: media; Owner: -
--

ALTER TABLE media.item_types ALTER COLUMN type_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME media.item_types_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: items; Type: TABLE; Schema: media; Owner: -
--

CREATE TABLE media.items (
    item_id integer NOT NULL,
    data_id uuid DEFAULT gen_random_uuid() NOT NULL,
    type_id integer NOT NULL,
    title text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    title_search text GENERATED ALWAYS AS (lower(NORMALIZE(title, NFKC))) STORED
);


--
-- Name: items_item_id_seq; Type: SEQUENCE; Schema: media; Owner: -
--

ALTER TABLE media.items ALTER COLUMN item_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME media.items_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: relation_types; Type: TABLE; Schema: media; Owner: -
--

CREATE TABLE media.relation_types (
    relation_type_id integer NOT NULL,
    data_id uuid DEFAULT gen_random_uuid() NOT NULL,
    relation_slug character varying(100) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: relation_types_relation_type_id_seq; Type: SEQUENCE; Schema: media; Owner: -
--

ALTER TABLE media.relation_types ALTER COLUMN relation_type_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME media.relation_types_relation_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: item_relations item_relations_from_item_id_relation_type_id_to_item_id_key; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_relations
    ADD CONSTRAINT item_relations_from_item_id_relation_type_id_to_item_id_key UNIQUE (from_item_id, relation_type_id, to_item_id);


--
-- Name: item_relations item_relations_pkey; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_relations
    ADD CONSTRAINT item_relations_pkey PRIMARY KEY (relation_id);


--
-- Name: item_types item_types_data_id_key; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_types
    ADD CONSTRAINT item_types_data_id_key UNIQUE (data_id);


--
-- Name: item_types item_types_item_type_slug_key; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_types
    ADD CONSTRAINT item_types_item_type_slug_key UNIQUE (item_type_slug);


--
-- Name: item_types item_types_pkey; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_types
    ADD CONSTRAINT item_types_pkey PRIMARY KEY (type_id);


--
-- Name: items items_data_id_key; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.items
    ADD CONSTRAINT items_data_id_key UNIQUE (data_id);


--
-- Name: items items_pkey; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.items
    ADD CONSTRAINT items_pkey PRIMARY KEY (item_id);


--
-- Name: relation_types relation_types_data_id_key; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.relation_types
    ADD CONSTRAINT relation_types_data_id_key UNIQUE (data_id);


--
-- Name: relation_types relation_types_pkey; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.relation_types
    ADD CONSTRAINT relation_types_pkey PRIMARY KEY (relation_type_id);


--
-- Name: relation_types relation_types_relation_slug_key; Type: CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.relation_types
    ADD CONSTRAINT relation_types_relation_slug_key UNIQUE (relation_slug);


--
-- Name: idx_item_relations_from; Type: INDEX; Schema: media; Owner: -
--

CREATE INDEX idx_item_relations_from ON media.item_relations USING btree (from_item_id);


--
-- Name: idx_item_relations_to; Type: INDEX; Schema: media; Owner: -
--

CREATE INDEX idx_item_relations_to ON media.item_relations USING btree (to_item_id);


--
-- Name: idx_items_title_bigm; Type: INDEX; Schema: media; Owner: -
--

CREATE INDEX idx_items_title_bigm ON media.items USING gin (title public.gin_bigm_ops);


--
-- Name: idx_items_title_search_bigm; Type: INDEX; Schema: media; Owner: -
--

CREATE INDEX idx_items_title_search_bigm ON media.items USING gin (title_search public.gin_bigm_ops);


--
-- Name: idx_items_type_id; Type: INDEX; Schema: media; Owner: -
--

CREATE INDEX idx_items_type_id ON media.items USING btree (type_id);


--
-- Name: items_source_uniq; Type: INDEX; Schema: media; Owner: -
--

CREATE UNIQUE INDEX items_source_uniq ON media.items USING btree (((metadata ->> 'source_table'::text)), ((metadata ->> 'source_id'::text)));


--
-- Name: item_types trg_item_types_updated_at; Type: TRIGGER; Schema: media; Owner: -
--

CREATE TRIGGER trg_item_types_updated_at BEFORE UPDATE ON media.item_types FOR EACH ROW EXECUTE FUNCTION media.set_updated_at();


--
-- Name: items trg_items_updated_at; Type: TRIGGER; Schema: media; Owner: -
--

CREATE TRIGGER trg_items_updated_at BEFORE UPDATE ON media.items FOR EACH ROW EXECUTE FUNCTION media.set_updated_at();


--
-- Name: relation_types trg_relation_types_updated_at; Type: TRIGGER; Schema: media; Owner: -
--

CREATE TRIGGER trg_relation_types_updated_at BEFORE UPDATE ON media.relation_types FOR EACH ROW EXECUTE FUNCTION media.set_updated_at();


--
-- Name: item_relations item_relations_from_item_id_fkey; Type: FK CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_relations
    ADD CONSTRAINT item_relations_from_item_id_fkey FOREIGN KEY (from_item_id) REFERENCES media.items(item_id);


--
-- Name: item_relations item_relations_relation_type_id_fkey; Type: FK CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_relations
    ADD CONSTRAINT item_relations_relation_type_id_fkey FOREIGN KEY (relation_type_id) REFERENCES media.relation_types(relation_type_id);


--
-- Name: item_relations item_relations_to_item_id_fkey; Type: FK CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_relations
    ADD CONSTRAINT item_relations_to_item_id_fkey FOREIGN KEY (to_item_id) REFERENCES media.items(item_id);


--
-- Name: item_types item_types_parent_type_id_fkey; Type: FK CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.item_types
    ADD CONSTRAINT item_types_parent_type_id_fkey FOREIGN KEY (parent_type_id) REFERENCES media.item_types(type_id);


--
-- Name: items items_type_id_fkey; Type: FK CONSTRAINT; Schema: media; Owner: -
--

ALTER TABLE ONLY media.items
    ADD CONSTRAINT items_type_id_fkey FOREIGN KEY (type_id) REFERENCES media.item_types(type_id);


--
-- PostgreSQL database dump complete
--

\unrestrict rcrf8cODGynrrAQDl8gkyUAnBNo65MJt2yirjZjHhrIaK5C4tceVfy7ZBdaR0Th

