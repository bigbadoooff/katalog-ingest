--
-- PostgreSQL database dump
--


-- Dumped from database version 17.11 (Debian 17.11-1.pgdg12+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg12+2)

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
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: abschnitt; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.abschnitt (
    id bigint NOT NULL,
    eintrag_id bigint NOT NULL,
    nr integer NOT NULL,
    text text NOT NULL,
    embedding public.vector(1024)
);


--
-- Name: abschnitt_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.abschnitt_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: abschnitt_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.abschnitt_id_seq OWNED BY public.abschnitt.id;


--
-- Name: beziehung; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.beziehung (
    id bigint NOT NULL,
    von_id bigint NOT NULL,
    zu_id bigint NOT NULL,
    art text NOT NULL,
    ableitung boolean DEFAULT true NOT NULL,
    begruendung text,
    erstellt_am timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: beziehung_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.beziehung_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: beziehung_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.beziehung_id_seq OWNED BY public.beziehung.id;


--
-- Name: eintrag; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.eintrag (
    id bigint NOT NULL,
    quelle text NOT NULL,
    quelle_id text NOT NULL,
    typ text NOT NULL,
    titel text,
    zusammenfassung text,
    inhalt_auszug text,
    zeitstempel timestamp with time zone,
    link text,
    tags text[] DEFAULT '{}'::text[],
    ableitung boolean DEFAULT false NOT NULL,
    status text DEFAULT 'neu'::text NOT NULL,
    geaendert_am timestamp with time zone DEFAULT now() NOT NULL,
    embedding public.vector(1024),
    felder jsonb,
    CONSTRAINT eintrag_status_check CHECK ((status = ANY (ARRAY['neu'::text, 'in_arbeit'::text, 'fertig'::text, 'fehler'::text])))
);


--
-- Name: eintrag_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.eintrag_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: eintrag_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.eintrag_id_seq OWNED BY public.eintrag.id;


--
-- Name: tag_klasse; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_klasse (
    tag text NOT NULL,
    klasse text NOT NULL,
    begruendung text,
    quelle text DEFAULT 'modell'::text NOT NULL,
    modell text,
    erstellt_am timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tag_klasse_klasse_check CHECK ((klasse = ANY (ARRAY['person'::text, 'vorgang'::text, 'kategorie'::text]))),
    CONSTRAINT tag_klasse_quelle_check CHECK ((quelle = ANY (ARRAY['modell'::text, 'manuell'::text])))
);


--
-- Name: vorschlag; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vorschlag (
    id bigint NOT NULL,
    art text NOT NULL,
    inhalt jsonb NOT NULL,
    quell_ids bigint[] DEFAULT '{}'::bigint[] NOT NULL,
    status text DEFAULT 'offen'::text NOT NULL,
    erstellt_am timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vorschlag_status_check CHECK ((status = ANY (ARRAY['offen'::text, 'uebernommen'::text, 'verworfen'::text])))
);


--
-- Name: vorschlag_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.vorschlag_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: vorschlag_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.vorschlag_id_seq OWNED BY public.vorschlag.id;


--
-- Name: abschnitt id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abschnitt ALTER COLUMN id SET DEFAULT nextval('public.abschnitt_id_seq'::regclass);


--
-- Name: beziehung id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.beziehung ALTER COLUMN id SET DEFAULT nextval('public.beziehung_id_seq'::regclass);


--
-- Name: eintrag id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eintrag ALTER COLUMN id SET DEFAULT nextval('public.eintrag_id_seq'::regclass);


--
-- Name: vorschlag id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vorschlag ALTER COLUMN id SET DEFAULT nextval('public.vorschlag_id_seq'::regclass);


--
-- Name: abschnitt abschnitt_eintrag_id_nr_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abschnitt
    ADD CONSTRAINT abschnitt_eintrag_id_nr_key UNIQUE (eintrag_id, nr);


--
-- Name: abschnitt abschnitt_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abschnitt
    ADD CONSTRAINT abschnitt_pkey PRIMARY KEY (id);


--
-- Name: beziehung beziehung_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.beziehung
    ADD CONSTRAINT beziehung_pkey PRIMARY KEY (id);


--
-- Name: beziehung beziehung_von_id_zu_id_art_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.beziehung
    ADD CONSTRAINT beziehung_von_id_zu_id_art_key UNIQUE (von_id, zu_id, art);


--
-- Name: eintrag eintrag_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eintrag
    ADD CONSTRAINT eintrag_pkey PRIMARY KEY (id);


--
-- Name: eintrag eintrag_quelle_quelle_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.eintrag
    ADD CONSTRAINT eintrag_quelle_quelle_id_key UNIQUE (quelle, quelle_id);


--
-- Name: tag_klasse tag_klasse_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_klasse
    ADD CONSTRAINT tag_klasse_pkey PRIMARY KEY (tag);


--
-- Name: vorschlag vorschlag_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vorschlag
    ADD CONSTRAINT vorschlag_pkey PRIMARY KEY (id);


--
-- Name: abschnitt_embedding_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX abschnitt_embedding_idx ON public.abschnitt USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: beziehung_von_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX beziehung_von_idx ON public.beziehung USING btree (von_id);


--
-- Name: beziehung_zu_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX beziehung_zu_idx ON public.beziehung USING btree (zu_id);


--
-- Name: eintrag_embedding_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX eintrag_embedding_idx ON public.eintrag USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: eintrag_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX eintrag_status_idx ON public.eintrag USING btree (status);


--
-- Name: eintrag_typ_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX eintrag_typ_idx ON public.eintrag USING btree (typ);


--
-- Name: vorschlag_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vorschlag_status_idx ON public.vorschlag USING btree (status);


--
-- Name: abschnitt abschnitt_eintrag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.abschnitt
    ADD CONSTRAINT abschnitt_eintrag_id_fkey FOREIGN KEY (eintrag_id) REFERENCES public.eintrag(id) ON DELETE CASCADE;


--
-- Name: beziehung beziehung_von_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.beziehung
    ADD CONSTRAINT beziehung_von_id_fkey FOREIGN KEY (von_id) REFERENCES public.eintrag(id) ON DELETE CASCADE;


--
-- Name: beziehung beziehung_zu_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.beziehung
    ADD CONSTRAINT beziehung_zu_id_fkey FOREIGN KEY (zu_id) REFERENCES public.eintrag(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


